"""One record per student: transcript, recommendation letters and their ranks.

A transcript is <student>.pdf (in pdf-folder) and a letter is
<student>_letter<N>.pdf (in letters-folder), so the student key is the
transcript's name without its suffix, and the letter's name without
``_letter<N>`` and its suffix.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from letter_builder import RANKS_CSV
from letter_builder.post_rank import post_rank

LETTER_SUFFIX = re.compile(r"_letter\d*$", re.IGNORECASE)
GROUP_RANKS = ["very excellent", "excellent", "very good", "good", "normal"]


def student_key(letter_name: str | Path) -> str:
    """adam_flores_letter2.pdf -> adam_flores; a name without _letter keeps its stem."""
    return LETTER_SUFFIX.sub("", Path(letter_name).stem)


def letter_ranks(folder: Path) -> dict[str, str]:
    """{letter stem: rank after post_rank} from <folder>/letter_ranks.csv."""
    return {Path(row["filename"]).stem: row["rank"] for row in post_rank(folder / RANKS_CSV)}


def group_rank(ranks: Iterable[str | None]) -> str | None:
    """A student's rank from their letters' (post_rank) ranks; unranked letters are ignored.

    very excellent: every letter excellent
    excellent:      at least one excellent, the rest good
    very good:      every letter good
    good:           at least one good (or excellent beside a normal one)
    normal:         every letter normal
    None when no letter is ranked yet.
    """
    ranks = [r for r in ranks if r]
    if not ranks:
        return None
    kinds = set(ranks)
    if kinds == {"excellent"}:
        return "very excellent"
    if kinds == {"excellent", "good"}:
        return "excellent"
    if kinds == {"good"}:
        return "very good"
    if kinds & {"excellent", "good"}:
        return "good"
    return "normal"


def student_info(transcript_names: Iterable[str | Path], letter_names: Iterable[str | Path],
                 ranks: Mapping[str, str] | None = None) -> list[dict]:
    """One record per student, sorted by student:

        {"student": "adam_flores", "transcript": "adam_flores.pdf",
         "letters": [{"file": "adam_flores_letter1.pdf", "rank": "good"}, ...],
         "group_rank": "very good"}

    ``ranks`` maps a letter's stem (adam_flores_letter1) to its rank, e.g.
    letter_ranks(letters_folder); a letter missing from it has rank None.
    Every student with a transcript or a letter gets a record: no letters is an
    empty list, no transcript is None.  Names keep only their final part.
    """
    ranks = ranks or {}
    records: dict[str, dict] = {}

    def record(student: str) -> dict:
        return records.setdefault(student, {"student": student, "transcript": None,
                                            "letters": [], "group_rank": None})

    for name in transcript_names:
        name = Path(name).name
        record(Path(name).stem)["transcript"] = name
    for name in letter_names:
        name = Path(name).name
        record(student_key(name))["letters"].append({"file": name, "rank": ranks.get(Path(name).stem)})
    for rec in records.values():
        rec["letters"].sort(key=lambda letter: letter["file"])
        rec["group_rank"] = group_rank(letter["rank"] for letter in rec["letters"])
    return [records[s] for s in sorted(records)]
