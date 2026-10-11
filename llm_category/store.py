"""The llm builder's per-course answer cache."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class AnswerCache:
    """Cached yes/no answers for one category, keyed by "code|title".

    Tied to the definition text and model: if either changes, old answers are
    discarded because they were given to a different question.
    """

    def __init__(self, path: Path, definition: str, model: str):
        self.path = path
        self.stamp = hashlib.sha256(f"{model}\n{definition}".encode()).hexdigest()[:16]
        self.answers: dict[str, bool] = {}
        # Seconds spent per LLM answer, for the Upload tab's time estimate.
        self.timing = {"count": 0, "seconds": 0.0}
        self.reset = False
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("stamp") == self.stamp:
                self.answers = saved["answers"]
                self.timing = saved.get("timing") or self.timing
            else:
                self.reset = True

    @staticmethod
    def key(code: str, title: str) -> str:
        return f"{code.strip().upper()}|{title.strip().lower()}"

    def get(self, code: str, title: str) -> bool | None:
        return self.answers.get(self.key(code, title))

    def put(self, code: str, title: str, answer: bool, seconds: float | None = None) -> None:
        self.answers[self.key(code, title)] = answer
        if seconds is not None:
            self.timing["count"] += 1
            self.timing["seconds"] += seconds
        self.save()  # every answer is ~10 s of LLM time; don't lose it on Ctrl-C

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"stamp": self.stamp, "answers": self.answers, "timing": self.timing},
                       indent=1, sort_keys=True), encoding="utf-8"
        )
