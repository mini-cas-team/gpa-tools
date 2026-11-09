"""Recommendation: the students' recommendation letters, ranked.

Every student with a letter PDF in <input-folder>/letters, named as
summary.names.letter_names does (their transcript's or profile's name). The
table shows two sub-columns under "Recommend Letter" (columns()), one per source:

    group-rank  Score  the group rank of their letters after post_rank: 100 (all
                       excellent), 90, 80, 70 or 60 (all normal); None until a
                       letter is ranked (letter_builder.students.group_rank)
    letters     Count  how many letters they have

rank() gives the Score; with no Transcript column, the page opens sorted by it.

``ranges`` filters: {source: (low, high)}, one inclusive range per SOURCES key
(either end may be None). A filter never drops a student: a value outside its
source's range (or none, no Score yet) is None, so only that sub-column's cell
is blank.
"""

from __future__ import annotations

from letter_builder import letters_folder, letters_work_folder
from letter_builder import students as letter_students
from summary import ABS, Filter, FilterSpec, RankItem, filtered, range_filters, range_params
from summary.names import letter_names

# source -> its sub-column heading
SOURCES = {"group-rank": "Score", "letters": "Count"}
# source -> (its query-parameter prefix, its highest value or None): ?score_min=&score_max= ...
FILTERS = {"group-rank": ("score", 100), "letters": ("count", None)}
TITLE = "Recommend Letter"
DESCRIPTION = "The applicant's recommendation letters: how strongly they recommend, and how many"
# source -> what it means: its sub-column's hover text
HINTS = {"group-rank": ("How strongly the letters recommend, together: 100 all excellent,"
                        " 90 excellent and good, 80 all good, 70 at least one good, 60 all normal"),
         "letters": "How many recommendation letters the applicant has"}

Range = tuple[float | None, float | None]


def range_label(low: float | None, high: float | None) -> str:
    """80, None -> "≥ 80"; 2, 3 -> "2–3"; no bound -> "" (no filter)."""
    fmt = lambda v: f"{v:g}"  # noqa: E731
    if low is not None and high is not None:
        return f"{fmt(low)}–{fmt(high)}"
    if low is not None:
        return f"≥ {fmt(low)}"
    if high is not None:
        return f"≤ {fmt(high)}"
    return ""


class Recommendation(ABS):
    def __init__(self, cfg: dict, ranges: dict[str, Range] | None = None):
        self.ranges: dict[str, Range] = {}
        for source, (low, high) in (ranges or {}).items():
            if source not in SOURCES:
                raise ValueError(f"unknown recommendation filter {source!r}; one of {', '.join(SOURCES)}")
            if low is not None and high is not None and low > high:
                raise ValueError(f"{source} range {low}–{high}: the low end is above the high end")
            if low is not None or high is not None:
                self.ranges[source] = (low, high)
        self.cfg = cfg
        self.title = TITLE
        self.description = DESCRIPTION
        self.folded_title = "/".join(SOURCES.values())  # "Score/Count"

    def _rows(self) -> list[tuple[str, dict[str, object]]]:
        """(student name, {source: value}) per student with a letter PDF; a value
        outside its source's range is None (only that sub-column's cell is blank)."""
        records = letter_students.folder_info(letters_folder(self.cfg),
                                              letters_work_folder(self.cfg), [])
        names = letter_names(self.cfg)
        rows = [(names.get(r["student"]) or r["student"],
                 {"group-rank": r["group_rank"], "letters": len(r["letters"])})
                for r in records if r["letters"]]
        return [(name, filtered(row, self.ranges)) for name, row in rows]

    def rank(self) -> list[RankItem]:
        return [RankItem(name, row["group-rank"]) for name, row in self._rows()]

    def hint(self, sub_title: str) -> str:
        return next((HINTS[s] for s, title in SOURCES.items() if title == sub_title), "")

    def columns(self) -> list[tuple[str, list[RankItem]]]:
        """Score, Count: one sub-column per source."""
        rows = self._rows()
        return [(title, [RankItem(name, row[source]) for name, row in rows])
                for source, title in SOURCES.items()]

    # ---- rank_ai: one range per source ----

    def filter_specs(self) -> list[FilterSpec]:
        return [FilterSpec(self.title, source, "range", HINTS[source],
                           low=0.0, high=None if top is None else float(top))
                for source, (_, top) in FILTERS.items()]

    def current_filters(self) -> list[Filter]:
        return range_filters(self.title, self.ranges)

    def params(self, filters: list[Filter]) -> list[tuple[str, str]]:
        return range_params(filters, {source: prefix for source, (prefix, _) in FILTERS.items()})
