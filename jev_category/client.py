"""Minimal TypeSafe client: one Noul question per course, many courses per request.

The request is the one tested in the TypeSafe Playground: the state holds the
category, its definition and the courses (course_01, course_02, ...), and each
question points at one course by name.  Jev answers every question in
parallel and returns, per question, the probability (0-1) that the answer is
yes.  API reference: https://docs.typesafe.ai/api.md
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

INSTRUCTIONS = "Using only `definition`, is the university course `{key}` in the category `category`?"
CRITERIA = {
    "true": "The course's subject matter falls within the academic subjects the definition includes.",
    "false": "The course's subject matter is outside the definition, or is one of the subjects "
             "the definition excludes.",
}
# 429 rate limited, 529 overloaded: the docs say to back off and retry.
RETRY_STATUSES = (429, 529)


class JevError(Exception):
    """The API refused the request; the message says why."""


class JevClient:
    def __init__(self, url: str, model: str, api_key: str, timeout: float = 120, retries: int = 4):
        self.url = url
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries

    def request_body(self, category: str, definition: str, courses: list[tuple[str, str]]) -> dict:
        width = max(2, len(str(len(courses))))
        state = {"category": category, "definition": definition}
        questions = {}
        for i, (code, title) in enumerate(courses, start=1):
            key = f"course_{i:0{width}d}"
            state[key] = f"{code} - {title}"
            questions[f"q{i:0{width}d}"] = {"type": "noul", "instructions": INSTRUCTIONS.format(key=key),
                                            "criteria": CRITERIA}
        return {"model": self.model, "state": state, "questions": questions}

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "gpa-tools",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code in RETRY_STATUSES and attempt < self.retries:
                    time.sleep(float(exc.headers.get("retry-after") or 2 ** attempt))
                    continue
                raise JevError(f"HTTP {exc.code}: {_detail(exc)}") from None
        raise AssertionError("unreachable")

    def answers(self, state, questions: dict) -> dict:
        """Jev's answer to each of ``questions`` (any question types) about ``state``, by name."""
        reply = self._post({"model": self.model, "state": state, "questions": questions})
        return reply.get("answers") or {}

    def scores(self, category: str, definition: str,
               courses: list[tuple[str, str]]) -> tuple[list[float], dict]:
        """(one 0-1 score per course, in order; the response's token usage)."""
        body = self.request_body(category, definition, courses)
        reply = self._post(body)
        answers = reply.get("answers") or {}
        try:
            scores = [float(answers[q]["noul"]) for q in body["questions"]]
        except (KeyError, TypeError, ValueError):
            raise JevError(f"unexpected response: {json.dumps(reply)[:300]}") from None
        return scores, reply.get("usage") or {}


def _detail(exc: urllib.error.HTTPError) -> str:
    """The error message in the response body, else the HTTP reason."""
    try:
        detail = json.loads(exc.read()).get("detail")
    except (ValueError, AttributeError, OSError):
        return str(exc.reason)
    if isinstance(detail, dict):
        return detail.get("message") or json.dumps(detail)
    return json.dumps(detail) if not isinstance(detail, str) else detail
