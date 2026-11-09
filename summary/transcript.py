"""Transcript: the uploaded transcripts, with three columns of a builder's results.

Holds every <work-folder>/transcripts/*.common.json. Each is matched to its row of
<work-folder>/<builder>/<cat>_gpa.csv by the transcript's file (source_file =
the CSV's ``file``) and named by its student.name. The table shows it as three
sub-columns under "Transcript" (columns()), one per source:

    category-gpa     <CAT>_GPA         STEM GPA          the category GPA (0-4)
    all-courses-gpa  all_courses_GPA   ALL GPA           the GPA over every course (0-4)
    category-pct     <cat>_all_pct     STEM CRS %        % of the student's courses in the category

rank() gives the ``by`` source's value; the page sorts by that sub-column first.

A transcript the builder has no value for has order None.

``ranges`` filters, whatever ``by`` is: {source: (low, high)}, one inclusive
range per SOURCES key (either end may be None). A filter never drops a
transcript: a value outside its source's range is None, so only that
sub-column's cell is blank and the other sub-columns keep their values.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import category_builder
from summary import ABS, RankItem, filtered

log = logging.getLogger(__name__)


def _float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


# by -> (its <cat>_gpa.csv column, its label); {CAT} is the category, {cat} lower case.
SOURCES = {
    "category-gpa": ("{CAT}_GPA", "{CAT} GPA"),
    "all-courses-gpa": ("all_courses_GPA", "ALL GPA"),
    "category-pct": ("{cat}_all_pct", "{CAT} CRS %"),
}
# by -> what it means ({CAT} is the category): its sub-column's hover text
SOURCE_HINTS = {
    "category-gpa": "GPA (0–4) of the applicant's {CAT} courses",
    "all-courses-gpa": "GPA (0–4) of all the applicant's courses",
    "category-pct": "Share of the applicant's courses that are {CAT} courses (0–100%)",
}
DEFAULT_SOURCE = "category-gpa"
DEFAULT_CATEGORY = "STEM"  # Student Rank's Transcript column, when the builder has it


def source_label(by: str, category: str) -> str:
    """category-pct, stem -> "STEM CRS %"."""
    return SOURCES[by][1].format(CAT=category.upper(), cat=category.lower())


def source_hint(by: str, category: str) -> str:
    """category-gpa, stem -> "GPA (0–4) of the applicant's STEM courses"."""
    return SOURCE_HINTS[by].format(CAT=category.upper())


def description(category: str = "") -> str:
    """The Transcript column's meaning, for ``category`` ("": the chosen one)."""
    return (f"The applicant's academic record: GPAs from their transcript's courses,"
            f" for the {category.upper() or 'chosen'} category")


# source -> (its query-parameter prefix, its highest value): ?gpa_min=&gpa_max= ...
FILTERS = {
    "category-gpa": ("gpa", 4.0),
    "all-courses-gpa": ("all_gpa", 4.0),
    "category-pct": ("pct", 100.0),
}

Range = tuple[float | None, float | None]


def range_label(source: str, low: float | None, high: float | None) -> str:
    """category-gpa, 3, None -> "≥ 3.00"; category-pct, 20, 50 -> "20–50%";
    no bound -> "" (no filter)."""
    pct = source == "category-pct"
    fmt = (lambda v: f"{v:g}") if pct else (lambda v: f"{v:.2f}")
    unit = "%" if pct else ""
    if low is not None and high is not None:
        return f"{fmt(low)}–{fmt(high)}{unit}"
    if low is not None:
        return f"≥ {fmt(low)}{unit}"
    if high is not None:
        return f"≤ {fmt(high)}{unit}"
    return ""


class Transcript(ABS):
    def __init__(self, cfg: dict, builder: str, category: str, by: str = DEFAULT_SOURCE,
                 ranges: dict[str, Range] | None = None):
        if by not in SOURCES:
            raise ValueError(f"unknown transcript source {by!r}; one of {', '.join(SOURCES)}")
        self.ranges: dict[str, Range] = {}
        for source, (low, high) in (ranges or {}).items():
            if source not in SOURCES:
                raise ValueError(f"unknown transcript filter {source!r}; one of {', '.join(SOURCES)}")
            if low is not None and high is not None and low > high:
                raise ValueError(f"{source} range {low}–{high}: the low end is above the high end")
            if low is not None or high is not None:
                self.ranges[source] = (low, high)
        self.category, self.by = category.upper(), by
        self.csv_columns = {source: column.format(CAT=self.category, cat=self.category.lower())
                        for source, (column, _) in SOURCES.items()}
        self.column = self.csv_columns[by]
        self.title = "Transcript"
        self.description = description(self.category)
        self.folded_title = f"{self.category}/ALL/%"  # the sub-columns folded into one
        work = category_builder.transcripts_folder(cfg)
        self.gpa_csv = category_builder.folder(cfg, builder) / f"{self.category.lower()}_gpa.csv"
        self.transcripts: list[dict] = []  # every uploaded transcript's common JSON
        for path in sorted(work.glob("*.common.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                log.warning("summary: %s skipped: %s", path.name, exc)
                continue
            if isinstance(data, dict):
                data.setdefault("source_file", path.name[:-len(".common.json")] + ".pdf")
                self.transcripts.append(data)

    def _values(self) -> dict[str, dict[str, float | None]]:
        """{transcript file: {source: its value}} from the builder's <cat>_gpa.csv."""
        if not self.gpa_csv.is_file():
            return {}
        with self.gpa_csv.open(newline="", encoding="utf-8") as fh:
            return {row.get("file") or "": {source: _float(row.get(column))
                                            for source, column in self.csv_columns.items()}
                    for row in csv.DictReader(fh)}

    def _rows(self) -> list[tuple[str, dict[str, float | None]]]:
        """(student name, {source: value}) per named transcript; a value outside
        its source's range is None (only that sub-column's cell is blank)."""
        values = self._values()
        rows = []
        for data in self.transcripts:
            name = ((data.get("student") or {}).get("name") or "").strip()
            if name:
                row = values.get(data["source_file"], {})
                rows.append((name, filtered({s: row.get(s) for s in SOURCES}, self.ranges)))
        return rows

    def rank(self) -> list[RankItem]:
        return [RankItem(name, row.get(self.by)) for name, row in self._rows()]

    def hint(self, sub_title: str) -> str:
        return next((source_hint(s, self.category) for s in SOURCES
                     if source_label(s, self.category) == sub_title), "")

    def columns(self) -> list[tuple[str, list[RankItem]]]:
        """STEM GPA, ALL GPA, STEM CRS %: one sub-column per source."""
        rows = self._rows()
        return [(source_label(source, self.category),
                 [RankItem(name, row.get(source)) for name, row in rows])
                for source in SOURCES]
