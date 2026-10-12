"""Try the definers in turn until one gives a definition, recording every try.

The attempts are the log the Upload tab shows and category_definer.log keeps:
each failure in order, then the success, or every failure when all fail.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Callable, Iterable


@dataclass
class Attempt:
    step: str  # "chatgpt", "gemini", "ollama"
    attempt: int  # 1-based, per step
    seconds: float
    error: str | None = None  # None: this try gave the definition

    def to_dict(self) -> dict:
        return asdict(self)

    def line(self) -> str:
        outcome = "ok" if self.error is None else f"failed: {self.error}"
        return f"{self.step} try {self.attempt} ({self.seconds:.1f}s) {outcome}"


@dataclass
class Draft:
    definition: str
    source: str  # the step that wrote it
    attempts: list[Attempt]

    @property
    def failures(self) -> list[Attempt]:
        return [a for a in self.attempts if a.error is not None]


class DraftFailed(OSError):
    """Every step failed. An OSError, so callers that already handle an
    unreachable Ollama report it the same way."""

    def __init__(self, category: str, attempts: list[Attempt]):
        self.attempts = attempts
        tries = "; ".join(a.line() for a in attempts) or "no steps configured"
        super().__init__(f"no definition for {category}: {tries}")


# (step name, number of tries, define(category) -> str)
Step = tuple[str, int, Callable[[str], str]]


def define(category: str, steps: Iterable[Step],
           on_attempt: Callable[[Attempt], None] | None = None) -> Draft:
    attempts: list[Attempt] = []
    for name, tries, fn in steps:
        for n in range(1, tries + 1):
            started = time.monotonic()
            try:
                definition = fn(category)
                if not (definition or "").strip():
                    raise ValueError("empty definition")
            except Exception as exc:  # any failure moves on to the next try
                attempt = Attempt(name, n, round(time.monotonic() - started, 1),
                                  f"{type(exc).__name__}: {exc}")
                attempts.append(attempt)
                if on_attempt:
                    on_attempt(attempt)
                continue
            attempt = Attempt(name, n, round(time.monotonic() - started, 1))
            attempts.append(attempt)
            if on_attempt:
                on_attempt(attempt)
            return Draft(definition.strip(), name, attempts)
    raise DraftFailed(category, attempts)
