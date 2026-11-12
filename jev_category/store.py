"""The jev builder's per-course score cache."""

from __future__ import annotations

from category_builder.cache import AnswerCache


class ScoreCache(AnswerCache):
    """Jev's 0-1 scores for one category, keyed by "code|title".

    AnswerCache's file layout and reset rules, keeping the score rather than an
    in/out answer, so changing the threshold needs no
    new API calls.  Saved once per request instead of once per answer.
    """

    answers: dict[str, float]

    def put_many(self, scores: dict[tuple[str, str], float], seconds: float) -> None:
        for (code, title), score in scores.items():
            self.answers[self.key(code, title)] = score
        self.timing["count"] += len(scores)
        self.timing["seconds"] += seconds
        self.save()
