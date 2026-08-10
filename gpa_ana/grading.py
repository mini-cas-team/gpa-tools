"""Grade scales, read from the transcript rather than assumed.

Scales are not shared across US institutions.  In a 14-file sample this package
was built against there were five distinct scales, including one where a plain
A is worth 5.00:

    A = 4.00  A- = 3.67  ...        (no A+)
    A+ = 4.00  A = 4.00  A- = 3.70  (A+ not rewarded)
    A+ = 4.30  A = 4.00  A- = 3.70
    A+ = 4.33  A = 4.00  A- = 3.67
    A = 5.00   B = 4.00   C = 3.00  (MIT)

Averaging those together unmodified would rank a 5.00-scale student far above
everyone else for identical work.  Every transcript prints its own GRADING KEY,
so the scale is parsed per file and normalised by the value the school assigns
to a plain A.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .model import Course, GradeScale

KEY_HEADING = re.compile(r"^\s*GRADING\s+KEY\s*$", re.IGNORECASE)
PAIR = re.compile(r"\b(?P<grade>[A-F][+-]?)\s*=\s*(?P<value>\d+(?:\.\d+)?)")
SCALE_NOTE = re.compile(r"scale\s+(?P<value>\d+(?:\.\d+)?)", re.IGNORECASE)


def parse_grading_key(lines: list[str]) -> GradeScale | None:
    """Read the printed GRADING KEY block, if the document has one."""
    values: dict[str, float] = {}
    collecting = False
    for line in lines:
        if KEY_HEADING.match(line):
            collecting = True
            continue
        if not collecting:
            continue
        if line.strip() and line.strip().isupper() and "=" not in line and len(line.strip()) > 4:
            break  # a new all-caps section heading ends the key
        for m in PAIR.finditer(line):
            values.setdefault(m.group("grade").upper(), float(m.group("value")))

    if not values:
        return None
    return GradeScale(values=values, anchor=values.get("A", 4.0), source="grading-key")


def derive_scale(courses: list[Course]) -> GradeScale | None:
    """Recover a scale from the rows themselves: quality points / credits.

    Used when a transcript prints no grading key.  Each row states its own
    quality points, so the per-grade value is recoverable as long as the rows
    parsed correctly -- which the validation gate checks independently.
    """
    buckets: dict[str, list[float]] = defaultdict(list)
    for course in courses:
        if course.quality_points is None or not course.credits or not course.grade_raw:
            continue
        buckets[course.grade_raw.upper()].append(course.quality_points / course.credits)

    values = {}
    for grade, samples in buckets.items():
        samples.sort()
        median = samples[len(samples) // 2]
        if max(samples) - min(samples) > 0.05:
            continue  # inconsistent -- do not invent a value
        values[grade] = round(median, 4)

    if not values:
        return None
    return GradeScale(values=values, anchor=values.get("A", 4.0), source="derived")


def resolve_scale(lines: list[str], courses: list[Course], profile) -> tuple[GradeScale | None, list[str]]:
    """Printed key first, then derivation, then a profile default."""
    warnings: list[str] = []

    scale = parse_grading_key(lines)
    if scale is None:
        scale = derive_scale(courses)
        if scale is not None:
            warnings.append("no GRADING KEY printed; scale derived from per-row quality points")

    if scale is None and profile.grade_scale:
        scale = GradeScale(
            values=dict(profile.grade_scale),
            anchor=profile.grade_scale.get("A", 4.0),
            source="profile-default",
        )
        warnings.append(f"scale taken from profile default ({profile.id})")

    if scale is None:
        warnings.append("no grade scale available: GPA cannot be computed")
    elif "A" not in scale.values:
        warnings.append("scale has no plain 'A'; normalisation anchor assumed to be 4.00")

    return scale, warnings


def points_for(course: Course, scale: GradeScale | None) -> float | None:
    """Quality points for one row, preferring the value the transcript printed."""
    if course.quality_points is not None:
        return course.quality_points
    if scale is None:
        return None
    per_credit = scale.points_for(course.grade_raw)
    return None if per_credit is None else per_credit * course.credits
