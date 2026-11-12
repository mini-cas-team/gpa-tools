"""Jev clients.  Both offer the same two calls, so the builder and the letter
ranking never know which one they have:

    scores(category, definition, courses) -> ([0-1 per course], usage)
    rank_letter(text)                     -> (excellent|good|normal, confidence)
    model                                 -> the Jev model that answers (stamps the caches)

JevClient (mode: direct, development only) calls TypeSafe itself and needs the
TypeSafe API key on this machine.  CloudClient (mode: cloud) calls the
gpa-tools cloud server (jev_cloud/), which holds the key, builds the same
request from jev_core/prompts.json, and shares course scores between clients;
this machine only needs a client token.  API reference:
https://docs.typesafe.ai/api.md
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import jev_core
from jev_core import JevError  # noqa: F401  (re-exported: callers import it from here)

# 429 rate limited, 529 overloaded (TypeSafe), 503 unavailable (cloud): back off and retry.
RETRY_STATUSES = (429, 503, 529)


def _post(url: str, body: dict | None, token: str | None, timeout: float, retries: int) -> dict:
    """POST ``body`` as JSON (GET when None) with a bearer token (none when None); the reply's JSON."""
    headers = {"Content-Type": "application/json", "User-Agent": "gpa-tools"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=None if body is None else json.dumps(body).encode(), headers=headers)
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUSES and attempt < retries:
                time.sleep(float(exc.headers.get("retry-after") or 2 ** attempt))
                continue
            raise JevError(f"HTTP {exc.code}: {_detail(exc)}") from None
    raise AssertionError("unreachable")


class JevClient:
    """mode: direct.  Calls TypeSafe with the API key."""

    def __init__(self, url: str, model: str, api_key: str, timeout: float = 120, retries: int = 4):
        self.url = url
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries

    def request_body(self, category: str, definition: str, courses: list[tuple[str, str]]) -> dict:
        return jev_core.course_body(self.model, category, definition, courses)

    def _post(self, body: dict) -> dict:
        return _post(self.url, body, self.api_key, self.timeout, self.retries)

    def answers(self, state, questions: dict) -> dict:
        """Jev's answer to each of ``questions`` (any question types) about ``state``, by name."""
        reply = self._post({"model": self.model, "state": state, "questions": questions})
        return reply.get("answers") or {}

    def scores(self, category: str, definition: str,
               courses: list[tuple[str, str]]) -> tuple[list[float], dict]:
        """(one 0-1 score per course, in order; the response's token usage)."""
        body = self.request_body(category, definition, courses)
        reply = self._post(body)
        return jev_core.parse_scores(body, reply), reply.get("usage") or {}

    def rank_letter(self, letter: str) -> tuple[str, float]:
        return jev_core.parse_choice(self._post(jev_core.letter_body(self.model, letter)))


class CloudClient:
    """mode: cloud.  Calls the gpa-tools cloud server with a client token.

    The server answers already-known courses from its shared score store and
    asks Jev only for the rest; ``usage`` then also says how many were ``cached``.
    """

    def __init__(self, url: str, token: str, timeout: float = 120, retries: int = 4):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self._info: dict | None = None

    def _call(self, path: str, body: dict | None = None) -> dict:
        return _post(f"{self.url}{path}", body, self.token, self.timeout, self.retries)

    def info(self) -> dict:
        """{model, limits}; also proves the server is up and accepts the token."""
        if self._info is None:
            self._info = self._call("/v1/info")
        return self._info

    @property
    def model(self) -> str:
        return self.info()["model"]

    def scores(self, category: str, definition: str,
               courses: list[tuple[str, str]]) -> tuple[list[float], dict]:
        reply = self._call("/v1/courses/score", {"category": category, "definition": definition,
                                                 "courses": [list(c) for c in courses]})
        try:
            scores = [float(s) for s in reply["scores"]]
        except (KeyError, TypeError, ValueError):
            raise JevError(f"unexpected response: {json.dumps(reply)[:300]}") from None
        if len(scores) != len(courses):
            raise JevError(f"asked about {len(courses)} course(s), got {len(scores)} score(s)")
        return scores, reply.get("usage") or {}

    def definition(self, category: str) -> str | None:
        """The cloud's shared definition of ``category``; None when it has none."""
        try:
            reply = self._call(f"/v1/definitions/{urllib.parse.quote(category.strip().upper())}")
        except JevError as exc:
            if str(exc).startswith("HTTP 404"):
                return None
            raise
        return (reply.get("definition") or "").strip() or None

    def definitions(self) -> dict[str, str]:
        """Every shared definition on the cloud: {CATEGORY: definition}."""
        return dict(self._call("/v1/definitions").get("definitions") or {})

    def create_definition(self, category: str, definition: str) -> dict:
        """Offer ``definition`` as the shared one for a new category.  Create-only: the
        reply is {category, definition, created}; when another client was first,
        ``created`` is False and ``definition`` is theirs, which this client should use."""
        reply = self._call(f"/v1/definitions/{urllib.parse.quote(category.strip().upper())}",
                           {"definition": definition})
        if not (reply.get("definition") or "").strip():
            raise JevError(f"unexpected answer: {json.dumps(reply)[:300]}")
        return reply

    def rank_letter(self, letter: str) -> tuple[str, float]:
        reply = self._call("/v1/letters/rank", {"letter": letter})
        if reply.get("choice") not in jev_core.LETTER_CHOICES:
            raise JevError(f"unexpected answer: {reply}")
        return reply["choice"], float(reply.get("confidence") or 0)


def _detail(exc: urllib.error.HTTPError) -> str:
    """The error message in the response body (TypeSafe's ``detail``, the cloud's
    ``error``), else the HTTP reason."""
    try:
        body = json.loads(exc.read())
    except (ValueError, AttributeError, OSError):
        return str(exc.reason)
    detail = body.get("detail", body.get("error")) if isinstance(body, dict) else None
    if detail is None:
        return str(exc.reason)
    if isinstance(detail, dict):
        return detail.get("message") or json.dumps(detail)
    return json.dumps(detail) if not isinstance(detail, str) else detail
