"""End-to-end: PDF -> validated, categorised Transcript.

No model is called anywhere in this package.  Reading a new transcript format
is a design-time activity that produces a profile YAML; at run time the app is
ordinary deterministic Python and its behaviour is reproducible.
"""

from __future__ import annotations

import re
from pathlib import Path

from .categories import Ruleset
from .extract import clean_lines, extract_pages
from .grading import resolve_scale
from .layout import Profile, dispatch
from .model import Transcript
from .validate import run_checks

TOTAL_LINE = re.compile(
    r"^Total\s+(?P<label>.+?)\s+Earned:\s*(?P<value>\d+(?:\.\d+)?)", re.IGNORECASE
)
CUM_GPA_LINE = re.compile(
    r"^Cumulative\s+Grade\s+Point\s+Average:\s*(?P<value>\d+(?:\.\d+)?)", re.IGNORECASE
)
PROGRAM_KEYS = (
    "field of concentration",
    "concentration",
    "course (major)",
    "major",
    "course",   # MIT prints "Course: Physics"; the table header has no colon
    "option",
    "program",
)
MIN_DISPATCH_SCORE = 10


def parse_transcript(
    path: Path,
    profiles: list[Profile],
    *,
    force_ocr: bool = False,
    dpi: int = 300,
    workers: int = 1,
) -> Transcript:
    pages = extract_pages(path, force_ocr=force_ocr, dpi=dpi, workers=workers)
    if not pages:
        raise RuntimeError(f"no pages extracted from {path}")

    page_lines = {p.number: clean_lines(p.text) for p in pages}
    all_lines = [ln for p in pages for ln in page_lines[p.number]]
    full_text = "\n".join(all_lines)

    profile, score = dispatch(full_text, profiles)

    transcript = Transcript(
        file=path.name,
        institution_id=profile.id,
        institution=profile.institution_for(all_lines),
        source="ocr" if any(p.source == "ocr" for p in pages) else "text",
    )
    if score < MIN_DISPATCH_SCORE:
        transcript.warnings.append(
            f"no layout profile matched confidently (score {score}); "
            f"parsed with the generic fallback -- needs review"
        )

    state: dict = {}
    fields: dict[str, str] = {}
    repairs: list[str] = []
    for page in pages:
        result = profile.module.parse(
            page_lines[page.number],
            file=path.name,
            page=page.number,
            profile=profile,
            state=state,
        )
        transcript.terms.extend(result["terms"])
        transcript.warnings.extend(result["warnings"])
        repairs.extend(result.get("repairs", []))
        state = result["state"]
        for key, value in result["fields"].items():
            fields.setdefault(key, value)

    if repairs:
        transcript.notes.append(
            f"{len(repairs)} grade cell(s) repaired from likely OCR glyph confusion "
            f"({'; '.join(repairs)}); each is still checked by the row_points gate"
        )

    _read_summary(all_lines, transcript)
    transcript.student = fields.get("name") or path.stem.replace("_", " ").title()
    for key in PROGRAM_KEYS:
        if key in fields:
            transcript.program = fields[key]
            break
    transcript.credit_label = next(
        (t.credit_label for t in transcript.terms if t.credit_label), None
    )

    scale, scale_warnings = resolve_scale(all_lines, transcript.courses, profile)
    transcript.scale = scale
    transcript.warnings.extend(scale_warnings)

    if not transcript.courses:
        transcript.warnings.append("no course rows parsed")

    transcript._validation = profile.validation  # consumed by run_checks
    run_checks(transcript)
    return transcript


def _read_summary(lines: list[str], transcript: Transcript) -> None:
    for line in lines:
        stripped = line.strip()
        if m := TOTAL_LINE.match(stripped):
            transcript.printed_total_credits = float(m.group("value"))
            transcript.credit_label = transcript.credit_label or m.group("label")
        elif m := CUM_GPA_LINE.match(stripped):
            transcript.printed_cumulative_gpa = float(m.group("value"))


def apply_categories(transcript: Transcript, ruleset: Ruleset) -> dict:
    """Tag every course with the ruleset's category. Returns an audit summary."""
    unmapped: dict[str, int] = {}
    disputed: list[str] = []

    for term in transcript.terms:
        tagged = []
        for course in term.courses:
            decision = ruleset.classify(course, transcript.institution_id)
            cats = set(course.categories)
            if decision.in_category:
                cats.add(ruleset.id)
            tagged.append(course.with_categories(frozenset(cats)))

            if decision.unmapped and course.dept:
                unmapped[course.dept] = unmapped.get(course.dept, 0) + 1
            if decision.disputed:
                disputed.append(f"{course.code or course.title}: {decision.reason} vs title")
        term.courses = tagged

    if unmapped:
        listing = ", ".join(f"{d} x{n}" for d, n in sorted(unmapped.items()))
        transcript.warnings.append(
            f"departments not in the {ruleset.id} ruleset: {listing} "
            f"-- extend categories/rulesets/{ruleset.id.lower()}.yaml"
        )
    if disputed:
        # The department map already won -- this says so out loud, because a
        # disagreement it never mentions is a wrong map that stays hidden.
        # Repeats are collapsed: a retaken course disputes once per sitting.
        counts: dict[str, int] = {}
        for entry in disputed:
            counts[entry] = counts.get(entry, 0) + 1
        listing = "; ".join(
            f"{entry} x{n}" if n > 1 else entry for entry, n in counts.items()
        )
        transcript.warnings.append(
            f"{ruleset.id} department map disputed by course title: {listing} "
            f"-- the map decides; confirm it or record why in "
            f"categories/rulesets/{ruleset.id.lower()}.yaml"
        )
    return {"unmapped": unmapped, "disputed": disputed}
