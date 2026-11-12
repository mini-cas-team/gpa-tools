"""The Jev questions and request format, shared by the client and the cloud server.

The question text lives in ``prompts.json`` so that the cloud server
(the private repo gpa-tools-cloud, JavaScript on Cloudflare Workers) and the client's direct mode
(this module) send TypeSafe exactly the same request; both test suites check
their output against ``fixtures.json``.  No network and no API key here.
"""

from __future__ import annotations

import json
from pathlib import Path

PROMPTS = json.loads((Path(__file__).with_name("prompts.json")).read_text(encoding="utf-8"))
COURSES = PROMPTS["courses"]
LETTERS = PROMPTS["letters"]
LIMITS = PROMPTS["limits"]
LETTER_CHOICES = tuple(LETTERS["criteria"])


class JevError(Exception):
    """Jev (or the cloud server in front of it) refused the request or answered oddly."""


def course_key(code: str, title: str) -> str:
    """How a course is cached, locally and in the cloud: "CODE|title"."""
    return f"{code.strip().upper()}|{title.strip().lower()}"


def course_body(model: str, category: str, definition: str, courses: list[tuple[str, str]]) -> dict:
    """One noul question per course: the state holds the category, its definition
    and the courses (course_01, ...); each question points at one course by name."""
    width = max(2, len(str(len(courses))))
    state = {"category": category, "definition": definition}
    questions = {}
    for i, (code, title) in enumerate(courses, start=1):
        key = f"course_{i:0{width}d}"
        state[key] = f"{code} - {title}"
        questions[f"q{i:0{width}d}"] = {"type": "noul",
                                        "instructions": COURSES["instructions"].replace("{key}", key),
                                        "criteria": COURSES["criteria"]}
    return {"model": model, "state": state, "questions": questions}


def parse_scores(body: dict, reply: dict) -> list[float]:
    """One 0-1 score per question of ``body``, in order."""
    answers = reply.get("answers") or {}
    try:
        return [float(answers[q]["noul"]) for q in body["questions"]]
    except (KeyError, TypeError, ValueError):
        raise JevError(f"unexpected response: {json.dumps(reply)[:300]}") from None


def letter_body(model: str, letter: str) -> dict:
    return {"model": model, "state": {"letter": letter},
            "questions": {"rank": {"type": "choice", "instructions": LETTERS["instructions"],
                                   "criteria": LETTERS["criteria"]}}}


def parse_choice(reply: dict) -> tuple[str, float]:
    """(excellent|good|normal, Jev's confidence)."""
    answer = (reply.get("answers") or {}).get("rank") or {}
    if answer.get("choice") not in LETTER_CHOICES:
        raise JevError(f"unexpected answer: {answer}")
    return answer["choice"], float(answer.get("confidence") or 0)
