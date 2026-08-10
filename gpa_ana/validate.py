"""Validation gates.

A transcript states its own answer -- printed term totals, printed cumulative
GPA, printed quality points per row.  Recomputing those from the parsed rows
gives a free ground-truth oracle on every file, including files nobody has
checked by hand, and it catches errors that raise no exception: a credit value
misread as 1 instead of 4 parses perfectly but breaks the recomputed total.

A pipeline that says "I could not read 6 of these 300" is trustworthy.  One
that silently mis-parses 6 and reports success is worse than useless, because
students get ranked on it.

Where a school prints no aggregate (Princeton publishes no GPA by policy), the
gate genuinely does not exist and the transcript is reported as ``none`` rather
than as passing.
"""

from __future__ import annotations

from .model import Check, Transcript

TOLERANCE_GPA = 0.011  # printed GPAs are rounded to 2 decimals
TOLERANCE_CREDITS = 0.01
TOLERANCE_POINTS = 0.02


def _gpa(courses) -> tuple[float | None, float, float]:
    credits = sum(c.credits for c in courses if c.counts_toward_gpa)
    points = sum(
        c.quality_points for c in courses if c.counts_toward_gpa and c.quality_points is not None
    )
    return ((points / credits) if credits else None), credits, points


def run_checks(transcript: Transcript) -> None:
    """Populate ``transcript.checks`` in place."""
    checks: list[Check] = []
    profile_validation = getattr(transcript, "_validation", {}) or {}

    checks.extend(_row_points_check(transcript))
    checks.extend(_term_checks(transcript, profile_validation))
    checks.extend(_totals_checks(transcript, profile_validation))

    transcript.checks = checks


def _row_points_check(transcript: Transcript) -> list[Check]:
    """Each row's printed points should equal scale(grade) x credits."""
    scale = transcript.scale
    if scale is None:
        return []
    bad = 0
    checked = 0
    for course in transcript.courses:
        expected_per_credit = scale.points_for(course.grade_raw)
        if expected_per_credit is None or course.quality_points is None:
            continue
        checked += 1
        if abs(expected_per_credit * course.credits - course.quality_points) > TOLERANCE_POINTS:
            bad += 1
    if not checked:
        return []
    return [
        Check(
            name="row_points",
            passed=bad == 0,
            printed=float(checked),
            recomputed=float(checked - bad),
            detail=f"{checked - bad}/{checked} rows agree with the printed grading key",
        )
    ]


def _term_checks(transcript: Transcript, validation: dict) -> list[Check]:
    checks: list[Check] = []
    credit_mismatch: list[str] = []
    gpa_mismatch: list[str] = []
    credit_terms = gpa_terms = 0

    for term in transcript.terms:
        recomputed_gpa, credits, _ = _gpa(term.courses)

        if term.printed_credits is not None:
            credit_terms += 1
            if abs(credits - term.printed_credits) > TOLERANCE_CREDITS:
                credit_mismatch.append(
                    f"{term.name}: printed {term.printed_credits:g}, parsed {credits:g}"
                )

        if term.printed_gpa is not None and validation.get("term_gpa", True):
            gpa_terms += 1
            if recomputed_gpa is None or abs(recomputed_gpa - term.printed_gpa) > TOLERANCE_GPA:
                gpa_mismatch.append(
                    f"{term.name}: printed {term.printed_gpa:.2f}, "
                    f"recomputed {recomputed_gpa if recomputed_gpa is None else f'{recomputed_gpa:.2f}'}"
                )

    if credit_terms:
        checks.append(
            Check(
                name="term_credits",
                passed=not credit_mismatch,
                printed=float(credit_terms),
                recomputed=float(credit_terms - len(credit_mismatch)),
                detail="; ".join(credit_mismatch)
                or f"{credit_terms}/{credit_terms} term credit totals match",
            )
        )
    if gpa_terms:
        checks.append(
            Check(
                name="term_gpa",
                passed=not gpa_mismatch,
                printed=float(gpa_terms),
                recomputed=float(gpa_terms - len(gpa_mismatch)),
                detail="; ".join(gpa_mismatch) or f"{gpa_terms}/{gpa_terms} term GPAs match",
            )
        )
    return checks


def _totals_checks(transcript: Transcript, validation: dict) -> list[Check]:
    checks: list[Check] = []
    recomputed_gpa, credits, _ = _gpa(transcript.courses)

    if transcript.printed_total_credits is not None:
        checks.append(
            Check(
                name="total_credits",
                passed=abs(credits - transcript.printed_total_credits) <= TOLERANCE_CREDITS,
                printed=transcript.printed_total_credits,
                recomputed=credits,
                detail=f"printed {transcript.printed_total_credits:g}, parsed {credits:g}",
            )
        )

    if transcript.printed_cumulative_gpa is not None and validation.get("cumulative_gpa", True):
        ok = recomputed_gpa is not None and (
            abs(recomputed_gpa - transcript.printed_cumulative_gpa) <= TOLERANCE_GPA
        )
        shown = "n/a" if recomputed_gpa is None else f"{recomputed_gpa:.4f}"
        checks.append(
            Check(
                name="cumulative_gpa",
                passed=ok,
                printed=transcript.printed_cumulative_gpa,
                recomputed=recomputed_gpa,
                detail=f"printed {transcript.printed_cumulative_gpa:.2f}, recomputed {shown}",
            )
        )

    return checks
