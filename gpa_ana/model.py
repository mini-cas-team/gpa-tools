"""Canonical intermediate representation.

This module is the contract between the two independent axes of the package:

    layout profiles/handlers  ->  WRITE these records, know nothing about categories
    category rulesets         ->  READ these records, know nothing about columns/OCR

Neither side imports the other. Adding a new institution touches only the
layout axis; adding a new taxonomy (medical, humanities, ...) touches only the
category axis.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class Provenance:
    """Where a record came from, so any number can be traced back to the page."""

    file: str
    page: int
    region: int = 0
    line: int | None = None
    source: str = "text"  # "text" (embedded text layer) or "ocr"
    confidence: float | None = None  # mean OCR word confidence, None for text layer


@dataclass(frozen=True)
class Course:
    """One row of a transcript, normalised across institutions."""

    title: str  # the only universally present field
    credits: float
    code: str | None = None  # "MATH 21A", "6.036", "AS.270.103"
    dept: str | None = None  # "MATH", "6", "270" -- extracted per code grammar
    credit_unit: str = "credit"  # printed label: credit/unit/course/point
    grade_raw: str = ""  # "B+", "A", "P"
    grade_points: float | None = None  # per-credit value on the *school's* scale
    quality_points: float | None = None  # printed points for the row
    counts_toward_gpa: bool = True
    term: str | None = None
    year_label: str | None = None
    categories: frozenset[str] = frozenset()  # filled by the category axis only
    provenance: Provenance | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def with_categories(self, cats: frozenset[str]) -> "Course":
        return replace(self, categories=cats)


@dataclass(frozen=True)
class GradeScale:
    """A grading scale as printed on the transcript itself.

    ``anchor`` is the value the school assigns to a plain "A"; it is what makes
    scales comparable.  MIT prints A = 5.00, Harvard A = 4.00.  Normalising by
    the anchor puts every school on a 4.0 basis.
    """

    values: dict[str, float]
    anchor: float = 4.0
    source: str = "grading-key"  # "grading-key" | "derived" | "profile-default"

    @property
    def factor(self) -> float:
        """Multiplier that converts this scale onto a 4.00 basis."""
        return 4.0 / self.anchor if self.anchor else 1.0

    def points_for(self, grade: str) -> float | None:
        return self.values.get(grade.strip().upper())


@dataclass
class Term:
    """One academic term, with whatever totals the transcript printed for it."""

    name: str
    year_label: str | None = None
    courses: list[Course] = field(default_factory=list)
    printed_credits: float | None = None
    printed_gpa: float | None = None
    credit_label: str | None = None


@dataclass
class Check:
    """One validation-gate result."""

    name: str
    passed: bool
    printed: float | None
    recomputed: float | None
    detail: str = ""

    @property
    def status(self) -> str:
        return "pass" if self.passed else "FAIL"


@dataclass
class Transcript:
    """Everything parsed out of one PDF."""

    file: str
    student: str | None = None
    institution: str | None = None
    institution_id: str | None = None  # profile id that claimed the file
    program: str | None = None
    terms: list[Term] = field(default_factory=list)
    scale: GradeScale | None = None
    printed_total_credits: float | None = None
    printed_cumulative_gpa: float | None = None
    credit_label: str | None = None
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Informational record of what the parser did -- kept in the saved output
    # for traceability, but not surfaced as something needing attention.
    notes: list[str] = field(default_factory=list)
    source: str = "text"

    @property
    def courses(self) -> list[Course]:
        return [c for t in self.terms for c in t.courses]

    @property
    def gate(self) -> str:
        """Overall validation state: pass / FAIL / none (nothing to check against)."""
        if not self.checks:
            return "none"
        return "pass" if all(c.passed for c in self.checks) else "FAIL"
