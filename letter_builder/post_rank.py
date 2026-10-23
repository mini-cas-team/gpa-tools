"""python -m letter_builder.post_rank [--folder DIR] [--config config.yaml]

Adjusts Jev's letter ranks by its confidence, in this order:

    good      below 0.7         -> normal
    excellent below 0.4         -> normal
    excellent 0.4 up to 0.7     -> good

so a letter only keeps a high rank when Jev was sure of it.  letter_ranks.csv
itself is never changed; the View page applies this when it reads the file.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from letter_builder import RANKS_CSV, parse_cli
from letter_builder.rank_letters import read_ranks

GOOD_MIN = 0.7
EXCELLENT_MIN = 0.7
EXCELLENT_TO_NORMAL_BELOW = 0.4


def adjust(rank: str, confidence: float | None) -> str:
    """The rank after the confidence rules above."""
    confidence = confidence or 0.0
    if rank == "good" and confidence < GOOD_MIN:
        return "normal"
    if rank == "excellent" and confidence < EXCELLENT_TO_NORMAL_BELOW:
        return "normal"
    if rank == "excellent" and confidence < EXCELLENT_MIN:
        return "good"
    return rank


def post_rank(path: Path) -> list[dict]:
    """letter_ranks.csv rows, each with ``jev_rank`` (as Jev answered) and ``rank`` (adjusted)."""
    rows = []
    for name, row in read_ranks(path).items():
        try:
            confidence = float(row.get("confidence") or 0)
        except ValueError:
            confidence = 0.0
        jev_rank = row.get("rank") or ""
        rows.append({"filename": name, "jev_rank": jev_rank, "confidence": confidence,
                     "rank": adjust(jev_rank, confidence)})
    return rows


def main(argv=None) -> int:
    _, _, folder = parse_cli(argparse.ArgumentParser(description=__doc__.splitlines()[0]), argv)
    rows = post_rank(folder / RANKS_CSV)
    for row in rows:
        if row["rank"] != row["jev_rank"]:
            print(f"{row['filename']}: {row['jev_rank']} -> {row['rank']} ({row['confidence']:.2f})")
    counts = Counter(row["rank"] for row in rows)
    print(f"{len(rows)} letters after post-ranking: {dict(counts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
