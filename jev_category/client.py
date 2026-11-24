"""Jev clients.  Both offer the same two calls, so the builder and the letter
ranking never know which one they have:

    scores(category, definition, courses) -> ([0-1 per course], usage)
    rank_letter(text)                     -> (excellent|good|normal, confidence)
    model                                 -> the Jev model that answers (stamps the caches)

JevClient (mode: direct, development only) calls TypeSafe itself and needs the
TypeSafe API key on this machine.  CloudClient (mode: cloud) calls the
gpa-tools cloud server (the private repo gpa-tools-cloud), which holds the key, builds the same
request from jev_core/prompts.json, and shares course scores between clients;
this machine only needs its key (jev_category/login.py).  API reference:
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
CONNECT_TIMEOUT = 15


class CloudError(JevError):
    """An HTTP error answer; ``status`` and the JSON ``body`` (the cloud's {error, ...})."""

    def __init__(self, message: str, status: int = 0, body: dict | None = None):
        super().__init__(message)
        self.status = status
        self.body = body if isinstance(body, dict) else {}


def _final(body) -> bool:
    """A 429 that waiting does not cure: a daily limit or no Update left."""
    return isinstance(body, dict) and ("n" in body or "limit" in str(body.get("error", "")))


def _post(url: str, body: dict | None, token: str | None, timeout: float, retries: int,
          method: str | None = None) -> dict:
    """POST ``body`` as JSON (GET when None; ``method`` overrides, e.g. DELETE) with a bearer
    token (none when None); the reply's JSON."""
    headers = {"Content-Type": "application/json", "User-Agent": "gpa-tools"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=None if body is None else json.dumps(body).encode(), headers=headers, method=method)
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = _body(exc)
            if exc.code in RETRY_STATUSES and attempt < retries and not _final(body):
                time.sleep(float(exc.headers.get("retry-after") or 2 ** attempt))
                continue
            raise CloudError(f"HTTP {exc.code}: {_detail(body, exc)}", exc.code, body) from None
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
    """mode: cloud.  Calls the gpa-tools cloud server with this computer's key.

    The server answers already-known courses from its shared score store and
    asks Jev only for the rest; ``usage`` then also says how many were ``cached``.
    Every limit is the key's email's, and one computer of an email works at a time
    (423 while another holds the account; ``connect``/``disconnect``).  The server scores
    a category only with the email's own text of it: 409 {definition, source} otherwise.
    """

    def __init__(self, url: str, token: str, timeout: float = 120, retries: int = 4):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self._info: dict | None = None

    def _call(self, path: str, body: dict | None = None, method: str | None = None,
              retries: int | None = None, timeout: float | None = None) -> dict:
        return _post(f"{self.url}{path}", body, self.token, timeout or self.timeout,
                     self.retries if retries is None else retries, method=method)

    @staticmethod
    def _cat(category: str) -> str:
        return urllib.parse.quote(category.strip().upper())

    def connect(self, since: str | None = None) -> dict:
        """Take the account's lock and get its definitions: {email, label, active, rev,
        definitions, entries, n} ({rev, unchanged, n} when ``since`` is still current).
        CloudError 423 (body in_use {label, until}) while another computer has it."""
        q = f"?since={urllib.parse.quote(since)}" if since else ""
        return self._call(f"/v1/connect{q}", {}, retries=0, timeout=CONNECT_TIMEOUT)

    def disconnect(self) -> bool:
        return bool(self._call("/v1/connect", None, method="DELETE", retries=0,
                               timeout=CONNECT_TIMEOUT).get("released"))

    def listing(self, since: str | None = None) -> dict:
        """GET /v1/definitions: what this email uses, as ``connect`` gives it."""
        q = f"?since={urllib.parse.quote(since)}" if since else ""
        return self._call(f"/v1/definitions{q}", retries=0, timeout=CONNECT_TIMEOUT)

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
        """This email's definition of ``category`` (its private one, else the shared one);
        None when there is none."""
        try:
            reply = self._call(f"/v1/definitions/{self._cat(category)}")
        except CloudError as exc:
            if exc.status == 404:
                return None
            raise
        return (reply.get("definition") or "").strip() or None

    def definitions(self) -> dict[str, str]:
        """Every definition this email uses: {CATEGORY: definition}."""
        return dict(self.listing().get("definitions") or {})

    def create_definition(self, category: str, definition: str, source: str | None = None) -> dict:
        """Add: offer an AI's (``source``: chatgpt, gemini, ...) ``definition`` of a new
        category as the shared one.  Create-only: the reply is {category, definition, source,
        ai, created}; when the category exists, ``created`` is False and ``definition`` is the
        existing text, which this computer uses."""
        body = {"definition": definition, **({"source": source} if source else {})}
        reply = self._call(f"/v1/definitions/{self._cat(category)}", body, retries=0)
        if not (reply.get("definition") or "").strip():
            raise JevError(f"unexpected answer: {json.dumps(reply)[:300]}")
        return reply

    def set_private(self, category: str, definition: str, base: str | None) -> dict:
        """Update -> Confirm: this email's own text of ``category`` (``base`` = the text shown
        for editing).  Uses one N; CloudError 429 when none is left, 409 (body: the current
        text) when it was changed elsewhere since."""
        return self._call(f"/v1/definitions/{self._cat(category)}/private",
                          {"definition": definition, "base": base}, method="PUT", retries=0)

    def reset_private(self, category: str) -> dict:
        """Reset: drop this email's own text; the shared one again."""
        return self._call(f"/v1/definitions/{self._cat(category)}/private", None,
                          method="DELETE", retries=0)

    def rank_letter(self, letter: str) -> tuple[str, float]:
        reply = self._call("/v1/letters/rank", {"letter": letter})
        if reply.get("choice") not in jev_core.LETTER_CHOICES:
            raise JevError(f"unexpected answer: {reply}")
        return reply["choice"], float(reply.get("confidence") or 0)


def _body(exc: urllib.error.HTTPError):
    try:
        return json.loads(exc.read())
    except (ValueError, AttributeError, OSError):
        return None


def _detail(body, exc: urllib.error.HTTPError) -> str:
    """The error message in the response body (TypeSafe's ``detail``, the cloud's
    ``error``), else the HTTP reason."""
    if body is None:
        return str(exc.reason)
    detail = body.get("detail", body.get("error")) if isinstance(body, dict) else None
    if detail is None:
        return str(exc.reason)
    if isinstance(detail, dict):
        return detail.get("message") or json.dumps(detail)
    return json.dumps(detail) if not isinstance(detail, str) else detail
