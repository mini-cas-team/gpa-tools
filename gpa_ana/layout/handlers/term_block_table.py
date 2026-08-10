"""Handler: term-blocked course table (the modal US registrar layout).

Shape this handler reads::

    YEAR 1: FRESHMAN YEAR
    Fall Term 2022
    Course        Title                       Grade   Credits   Points     <- header
    MATH 21A      Multivariable Calculus      B-           4     10.68     <- rows
    Credits: 16 Term GPA: 2.58 Cumulative Credits: 16 Cumulative GPA: 2.58 <- term totals

Column count is **discovered from the header row**, never assumed.  The header
names the bands; each data row is split on runs of 2+ spaces and every token is
assigned to the band it overlaps.  A seven-column transcript is just a header
with seven entries.

Semantic roles are resolved by position relative to the ``Grade`` band, because
labels are not unique -- Columbia prints ``Grade  Points  Points`` where the
first "Points" means credits and the second means quality points.
"""

from __future__ import annotations

import re

from ...model import Course, Provenance, Term

# ---------------------------------------------------------------------------
# line recognisers
# ---------------------------------------------------------------------------

TERM_LINE = re.compile(
    r"^(Fall|Spring|Winter|Autumn|Summer)\s+(Term|Quarter|Semester|Session)?\s*(\d{4})$",
    re.IGNORECASE,
)
YEAR_LINE = re.compile(r"^YEAR\s+\d+\s*:\s*(?P<label>.+?)\s*$", re.IGNORECASE)
HEADER_LINE = re.compile(r"^Course\s{2,}.*\bGrade\b", re.IGNORECASE)
TERM_TOTAL = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z ]*?):\s*(?P<credits>\d+(?:\.\d+)?)"
    r"(?:\s+Term GPA:\s*(?P<gpa>\d+(?:\.\d+)?))?",
)
FIELD_LINE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z ()]*?):\s*(?P<value>.+?)\s*$")

# Trailing sections that end the course table. Without this the summary block's
# "Cumulative Grade Point Average: 3.35" reads as a term credit total and
# silently overwrites the last term's real figure.
SECTION_END = re.compile(
    r"^(SUMMARY\s+OF\s+RECORD|GRADING\s+KEY|NOTES?|KEY\s+TO\s+GRADES|"
    r"DEGREES?\s+AWARDED|TRANSFER\s+CREDIT|END\s+OF\s+(OFFICIAL\s+)?TRANSCRIPT)\b",
    re.IGNORECASE,
)
SUMMARY_LABEL = re.compile(r"^(cumulative|total|overall)\b", re.IGNORECASE)

PROGRAM_KEYS = (
    "major",
    "concentration",
    "field of concentration",
    "option",
    "course (major)",
    "program",
    "degree program",
)


def _spans(line: str) -> list[tuple[int, int, str]]:
    """Split on runs of 2+ spaces, keeping each token's character span.

    A cell is a maximal run containing no double space, so multi-word values
    ("Introduction to Sociology", "CIV ENG 100") stay intact while the wide
    gaps between columns separate them.
    """
    return [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+(?: \S+)*", line)]


# Column labels are not always separated by two spaces: Yale prints
# "Grade Course Credits" with a single space, which would otherwise read as one
# band.  Grade is the pivot the semantic roles are resolved against, so a band
# that begins with it is split back apart.
HEADER_PIVOT = re.compile(r"^(?P<pivot>Grade)\s+(?P<rest>\S.*)$", re.IGNORECASE)


def parse_header(line: str) -> list[dict]:
    """Header row -> discovered column bands, in printed order."""
    columns: list[dict] = []
    for start, end, text in _spans(line):
        m = HEADER_PIVOT.match(text)
        if m:
            pivot, rest = m.group("pivot"), m.group("rest")
            columns.append({"start": start, "end": start + len(pivot), "label": pivot})
            columns.append({"start": end - len(rest), "end": end, "label": rest})
        else:
            columns.append({"start": start, "end": end, "label": text})
    return columns


# Column positions are not exact on a scanned page: OCR word coordinates are
# converted back to character columns using a median character width, so
# absolute offsets drift by a character or two along a line.  A grade landing
# one column short of its own band must still read as a grade.
BOUNDARY_TOLERANCE = 2


def _column_for(start: int, columns: list[dict]) -> int:
    """Which column's territory a token at ``start`` falls into.

    A column owns everything from its own label start to the next label's
    start.  Territory, not label extent, is what matters: a label is much
    narrower than the content beneath it ("Title" is 5 characters over a
    60-character course name), so binding cells to the nearest *label* misfiles
    any fragment sitting in the empty middle -- exactly what happens when an
    OCR double-space splits a long title and the tail drifts toward "Grade".
    """
    index = 0
    for position, column in enumerate(columns[1:], start=1):
        if column["start"] - BOUNDARY_TOLERANCE <= start:
            index = position
    return index


def assign(line: str, columns: list[dict]) -> dict[int, str]:
    """Map each token of a data row onto the column it belongs to."""
    out: dict[int, str] = {}

    for start, end, text in _spans(line):
        # A token overlapping a label belongs to it -- this keeps a right-aligned
        # number wider than its own label from drifting one column left.
        overlap, index = max(
            (min(end, col["end"]) - max(start, col["start"]), i)
            for i, col in enumerate(columns)
        )
        if overlap <= 0:
            index = _column_for(start, columns)
        out[index] = f"{out[index]} {text}".strip() if index in out else text

    return out


def _gap(start: int, end: int, col: dict) -> int:
    if end <= col["start"]:
        return col["start"] - end
    if start >= col["end"]:
        return start - col["end"]
    return 0


def _roles(columns: list[dict]) -> dict | None:
    """Resolve column indices to semantic roles. Returns None if unrecognisable."""
    grade_idx = next(
        (i for i, c in enumerate(columns) if c["label"].strip().lower() == "grade"), None
    )
    if grade_idx is None or grade_idx < 2:
        return None
    trailing = list(range(grade_idx + 1, len(columns)))
    if not trailing:
        return None
    return {
        "code": 0,
        "title": list(range(1, grade_idx)),
        "grade": grade_idx,
        "credits": trailing[0],
        "points": trailing[-1] if len(trailing) > 1 else None,
        "other": trailing[1:-1],
        "credit_label": columns[trailing[0]]["label"],
    }


def _number(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group()) if m else None


GRADE_TOKEN = re.compile(r"^(?:[A-DF][+-]?|P|CR|NC|W|S|U|I|AUD|PA|FL|EXLD)$", re.IGNORECASE)
NON_GPA_GRADES = {"P", "CR", "NC", "W", "S", "U", "I", "AUD", "PA", "FL", "EXLD"}

# Glyph confusions seen on scanned pages: the "+" of a B+ is routinely read as
# a "t". A repair is only attempted when the cell is not already a valid grade,
# and every repaired row is still checked by the row_points gate -- scale(grade)
# x credits must equal the points printed on that row -- so a wrong repair
# fails loudly instead of quietly changing someone's GPA.
GRADE_SUFFIX_FIXES = {"T": "+", "4": "+", "#": "+", "~": "-", "_": "-", "—": "-", "–": "-"}
GRADE_LETTER_FIXES = {"8": "B", "6": "C", "0": "D"}


def normalise_grade(token: str) -> tuple[str | None, str | None]:
    """Return (grade, repaired_from). ``grade`` is None if unrecognisable."""
    token = token.strip()
    if GRADE_TOKEN.match(token):
        return token.upper(), None

    if len(token) in (1, 2):
        letter = GRADE_LETTER_FIXES.get(token[0], token[0]).upper()
        suffix = ""
        if len(token) == 2:
            # An already-valid modifier passes through, so a row whose *letter*
            # was misread ("8+") is still recoverable.
            suffix = (
                token[1]
                if token[1] in "+-"
                else GRADE_SUFFIX_FIXES.get(token[1].upper(), "")
            )
            if not suffix:
                return None, None
        candidate = letter + suffix
        if candidate != token.upper() and GRADE_TOKEN.match(candidate):
            return candidate, token

    return None, None


def _refit_carried_bands(lines, columns, roles, *, file, page, term, profile):
    """Re-fit bands carried from the previous page to this page's own header.

    Only fires when a term is still open, this page prints a header of its own
    further down, the two geometries disagree, and at least one of the rows
    stranded above that header actually parses under the new bands.  Those four
    conditions together mean the carried bands are demonstrably wrong here; any
    weaker test would re-fit pages that were parsing correctly.

    Returns ``(columns, roles, refitted)`` -- the inputs unchanged when it does
    not fire.
    """
    header_at = next((i for i, l in enumerate(lines) if HEADER_LINE.match(l.strip())), None)
    if not header_at:  # None, or 0 -> nothing was stranded above it
        return columns, roles, False

    page_columns = parse_header(lines[header_at])
    page_roles = _roles(page_columns)
    if page_roles is None:
        return columns, roles, False
    if [c["start"] for c in page_columns] == [c["start"] for c in columns]:
        return columns, roles, False

    stranded = [l.rstrip() for l in lines[:header_at] if l.strip()]
    recovered = any(
        _row(line, page_columns, page_roles, file=file, page=page, lineno=0,
             term=term, profile=profile)
        for line in stranded
    )
    if not recovered:
        return columns, roles, False
    return page_columns, page_roles, True


def parse(lines: list[str], *, file: str, page: int, profile, state: dict | None = None) -> dict:
    """Read one page.

    ``state`` carries the open term, its column bands and the current year
    label across page breaks, since a term may continue onto the next page
    without reprinting its header.  Pass the returned ``state`` to the next
    page of the same document.

    Carrying the bands assumes both pages share a column layout, which a
    registrar does not guarantee -- each page is re-flowed independently, so a
    continued term's rows can sit several characters off the geometry of the
    page they started on.  Where this page prints its own header and it
    disagrees, the carried bands are re-fitted to it rather than silently
    dropping the rows they no longer match.
    """
    state = state if state is not None else {}
    terms: list[Term] = []
    current: Term | None = state.get("current")
    columns: list[dict] | None = state.get("columns")
    roles: dict | None = state.get("roles")
    year_label: str | None = state.get("year_label")
    fields: dict[str, str] = {}
    warnings: list[str] = []
    repaired: list[str] = []

    if current is not None and columns is not None:
        columns, roles, refitted = _refit_carried_bands(
            lines, columns, roles, file=file, page=page, term=current, profile=profile
        )
        if refitted:
            warnings.append(
                f"{file} p{page}: term continued across the page break under a "
                f"different column layout; re-fitted to this page's header"
            )

    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if SECTION_END.match(stripped):
            current, columns, roles = None, None, None
            continue

        if m := YEAR_LINE.match(stripped):
            year_label = m.group("label")
            continue

        if m := TERM_LINE.match(stripped):
            current = Term(name=stripped, year_label=year_label)
            terms.append(current)
            columns, roles = None, None
            continue

        if HEADER_LINE.match(stripped):
            columns = parse_header(line)
            roles = _roles(columns)
            if roles is None:
                warnings.append(f"{file} p{page}: unreadable column header: {stripped!r}")
            elif current is not None:
                current.credit_label = roles["credit_label"]
            continue

        if current is not None and columns and roles:
            course = _row(line, columns, roles, file=file, page=page, lineno=lineno,
                          term=current, profile=profile)
            if course is not None:
                current.courses.append(course)
                if "grade_repaired_from" in course.extra:
                    repaired.append(
                        f"{course.code or course.title}: "
                        f"{course.extra['grade_repaired_from']!r}->{course.grade_raw}"
                    )
                continue

        if current is not None and (m := TERM_TOTAL.match(stripped)):
            # "Credits: 16 Term GPA: 2.58" is a term total; "Cumulative ...: 3.35"
            # belongs to the document summary, not to this term.
            if m.group("credits") is not None and not SUMMARY_LABEL.match(m.group("label")):
                current.printed_credits = _number(m.group("credits"))
                current.printed_gpa = _number(m.group("gpa"))
                if current.credit_label is None:
                    current.credit_label = m.group("label").strip()
                continue

        if m := FIELD_LINE.match(stripped):
            fields.setdefault(m.group("key").strip().lower(), m.group("value").strip())

    state.update(current=current, columns=columns, roles=roles, year_label=year_label)
    return {
        "terms": terms,
        "fields": fields,
        "warnings": warnings,
        "repairs": repaired,
        "state": state,
    }


def _row(line, columns, roles, *, file, page, lineno, term, profile) -> Course | None:
    cells = assign(line, columns)
    grade, repaired_from = normalise_grade(cells.get(roles["grade"]) or "")
    if grade is None:
        return None

    code = (cells.get(roles["code"]) or "").strip() or None
    title = " ".join(
        (cells.get(i) or "").strip() for i in roles["title"] if cells.get(i)
    ).strip()
    if not title:
        return None

    credits = _number(cells.get(roles["credits"]))
    if credits is None:
        return None
    points = _number(cells.get(roles["points"])) if roles["points"] is not None else None

    grade_up = grade.upper()
    counts = grade_up not in NON_GPA_GRADES
    extra = {
        columns[i]["label"]: cells[i] for i in roles["other"] if i in cells
    }
    if repaired_from:
        extra["grade_repaired_from"] = repaired_from

    return Course(
        title=title,
        credits=credits,
        code=code,
        dept=profile.dept_of(code) if code else None,
        credit_unit=(roles["credit_label"] or "credit").rstrip("s").lower(),
        grade_raw=grade_up,
        quality_points=points,
        counts_toward_gpa=counts,
        term=term.name,
        year_label=term.year_label,
        provenance=Provenance(file=file, page=page, line=lineno),
        extra=extra,
    )
