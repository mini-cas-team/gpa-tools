"""Aggregate category GPA per student and rank.

Every student is ranked by one rule: **capped** GPA.  Each course is rescaled so
that a plain A is worth 4.00 at every school, then capped at 4.00.

The two steps answer the two ways a scale can distort a cross-school
comparison.  Rescaling handles schools that print a different maximum -- MIT
prints A = 5.00, and without rescaling it would sweep the table for identical
work.  Capping handles schools that award A+ *above* 4.00, a ceiling students
elsewhere cannot reach however well they do.

What capping deliberately gives up: it equalises the ceiling, not the
distribution.  Grade inflation, curve severity and department difficulty are
untouched, and straight-A+ is indistinguishable from straight-A once both land
on 4.000.  The figure is comparable; the achievement behind it is not
necessarily.

``raw`` -- the average on the school's own printed scale -- is still reported
alongside, because it is the only number that can be checked against the
transcript as printed.  It is never used for ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Transcript

CAP = 4.0


@dataclass
class StudentResult:
    student: str
    file: str
    institution: str | None
    institution_id: str | None
    program: str | None
    category: str
    category_courses: int = 0
    category_credits: float = 0.0
    total_courses: int = 0
    category_course_pct: float | None = None
    gpa_raw: float | None = None
    gpa_capped: float | None = None
    overall_gpa_capped: float | None = None
    scale_anchor: float | None = None
    scale_source: str | None = None
    credit_label: str | None = None
    gate: str = "none"
    failed_checks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    courses: list[dict] = field(default_factory=list)

    @property
    def rankable(self) -> bool:
        return self.gpa_capped is not None and self.gate != "FAIL"


def _weighted(pairs: list[tuple[float, float]]) -> float | None:
    """pairs of (per-credit grade points, credits) -> weighted mean."""
    credits = sum(c for _, c in pairs)
    if not credits:
        return None
    return sum(g * c for g, c in pairs) / credits


def summarise(transcript: Transcript, category: str) -> StudentResult:
    scale = transcript.scale
    factor = scale.factor if scale else 1.0

    in_category: list[tuple[float, float]] = []
    everything: list[tuple[float, float]] = []
    rows: list[dict] = []

    for course in transcript.courses:
        per_credit = (
            course.quality_points / course.credits
            if course.quality_points is not None and course.credits
            else (scale.points_for(course.grade_raw) if scale else None)
        )
        tagged = category in course.categories
        rows.append(
            {
                "code": course.code,
                "title": course.title,
                "term": course.term,
                "grade": course.grade_raw,
                "credits": course.credits,
                "credit_unit": course.credit_unit,
                "quality_points": course.quality_points,
                "grade_points_4pt": round(per_credit * factor, 4) if per_credit is not None else None,
                "dept": course.dept,
                category.lower(): tagged,
                "counts_toward_gpa": course.counts_toward_gpa,
            }
        )
        if per_credit is None or not course.counts_toward_gpa:
            continue
        everything.append((per_credit, course.credits))
        if tagged:
            in_category.append((per_credit, course.credits))

    raw = _weighted(in_category)
    capped = _weighted([(min(g * factor, CAP), c) for g, c in in_category])
    overall = _weighted([(min(g * factor, CAP), c) for g, c in everything])

    total_courses = len(everything)

    result = StudentResult(
        student=transcript.student or transcript.file,
        file=transcript.file,
        institution=transcript.institution,
        institution_id=transcript.institution_id,
        program=transcript.program,
        category=category,
        category_courses=len(in_category),
        category_credits=sum(c for _, c in in_category),
        total_courses=total_courses,
        category_course_pct=(
            round(100.0 * len(in_category) / total_courses, 2) if total_courses else None
        ),
        gpa_raw=_round(raw),
        gpa_capped=_round(capped),
        overall_gpa_capped=_round(overall),
        scale_anchor=scale.anchor if scale else None,
        scale_source=scale.source if scale else None,
        credit_label=transcript.credit_label,
        gate=transcript.gate,
        failed_checks=[f"{c.name}: {c.detail}" for c in transcript.checks if not c.passed],
        warnings=list(transcript.warnings),
        notes=list(transcript.notes),
        courses=rows,
    )
    return result


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def rank(results: list[StudentResult], *, min_courses: int = 0):
    """Split into a ranked table and a held-back list, never silently dropping."""
    ranked, held = [], []
    for result in results:
        value = result.gpa_capped
        if value is None:
            held.append((result, "no category GPA could be computed"))
        elif result.gate == "FAIL":
            held.append((result, "failed validation gate"))
        elif result.category_courses < min_courses:
            plural = "" if result.category_courses == 1 else "s"
            held.append((result, f"only {result.category_courses} category course{plural}"))
        else:
            ranked.append(result)

    ranked.sort(key=lambda r: (-(r.gpa_capped or 0), r.student))
    return ranked, held
