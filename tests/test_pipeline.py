"""Tests for gpa-ana.

The unit tests cover the parts that actually bit during development: column
discovery on headers whose labels collide or run together, the per-institution
code grammars, and scale normalisation.  The integration test re-parses the
whole configured corpus and asserts every validation gate still passes -- it is
the regression net for adding a new profile.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gpa_ana.categories import load_ruleset
from gpa_ana.config import Config
from gpa_ana.grading import parse_grading_key
from gpa_ana.layout import Profile, load_profiles
from gpa_ana.extract import _detect_regions
from gpa_ana.layout.handlers.term_block_table import (
    assign,
    normalise_grade,
    parse_header,
    _roles,
    _spans,
)
from gpa_ana.model import Course
from gpa_ana.pipeline import apply_categories, parse_transcript
from gpa_ana.rank import rank, summarise

CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


# --------------------------------------------------------------------------
# column discovery
# --------------------------------------------------------------------------


def test_spans_split_on_double_space_only():
    line = "GOV 10                  Foundations of Political Theory     B-      4    10.68"
    assert [t for _, _, t in _spans(line)] == [
        "GOV 10",
        "Foundations of Political Theory",
        "B-",
        "4",
        "10.68",
    ]


def test_duplicate_points_header_resolves_by_position():
    """Columbia prints 'Grade  Points  Points': the first means credits."""
    header = "Course          Title                     Grade   Points   Points"
    roles = _roles(parse_header(header))
    assert roles["credits"] == 3 and roles["points"] == 4
    assert roles["credit_label"] == "Points"


def test_single_space_header_labels_are_split():
    """Yale prints 'Grade Course Credits' with one space between labels."""
    header = "Course        Title                    Grade Course Credits   Points"
    columns = parse_header(header)
    assert [c["label"] for c in columns] == [
        "Course", "Title", "Grade", "Course Credits", "Points",
    ]
    roles = _roles(columns)
    assert roles["credit_label"] == "Course Credits"


def test_seven_column_header_is_not_special_cased():
    header = "Course     Title             Grade   Units   Points   Term   Flag"
    roles = _roles(parse_header(header))
    assert roles["credits"] == 3 and roles["points"] == 6
    assert roles["other"] == [4, 5]  # extra columns preserved, not dropped


def test_right_aligned_number_binds_to_its_label():
    header = "Course        Title              Grade   Credits   Points"
    row = "MATH 21A      Multivariable      B-           4    10.68"
    cells = assign(row, parse_header(header))
    assert cells[2] == "B-" and cells[3] == "4" and cells[4] == "10.68"


# --------------------------------------------------------------------------
# scanned pages
# --------------------------------------------------------------------------


def test_ocr_glyph_confusion_in_grade_is_repaired():
    """A scan reads the '+' of B+ as a 't'."""
    assert normalise_grade("Bt") == ("B+", "Bt")
    assert normalise_grade("Ct") == ("C+", "Ct")
    assert normalise_grade("8+") == ("B+", "8+")
    assert normalise_grade("B+") == ("B+", None)  # already valid, untouched
    assert normalise_grade("XQ") == (None, None)  # not repairable, not guessed


def test_title_fragment_split_by_ocr_stays_in_the_title():
    """An OCR double-space splits a title; the tail must not become the grade."""
    header = "  Course        Title                                    Grade   Credits   Points"
    row = "  HUM 10A       A Colloquium: From Homer  to Joyce          B+          4    13.32"
    columns = parse_header(header)
    cells = assign(row, columns)
    roles = _roles(columns)
    assert cells[roles["grade"]] == "B+"
    assert "to Joyce" in cells[roles["title"][0]]


def test_grade_one_column_short_still_binds_to_grade():
    """Rebuilt OCR columns drift by a character; a 'B' at 106 belongs at 107."""
    header = " Course      Title                Grade   Credits   Points"
    columns = parse_header(header)
    grade_start = next(c["start"] for c in columns if c["label"] == "Grade")
    row = (
        " MATH 155R   Combinatorics".ljust(grade_start - 1)
        + "B".ljust(8)
        + "4    12.00"
    )
    assert assign(row, columns)[_roles(columns)["grade"]] == "B"


def test_regions_partition_the_page_without_dropping_columns():
    """Narrow segments merge into a neighbour -- they are never discarded."""
    Image = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw

    width, height = 1200, 400
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    for left, right in [(20, 380), (700, 760), (900, 1180)]:  # middle is a sliver
        draw.rectangle([left, 10, right, height - 10], fill=0)

    path = Path(tempfile.mkdtemp()) / "page.png"
    image.save(path)
    boxes = _detect_regions(path)

    covered = sorted((left, left + w) for left, _, w, _ in boxes)
    assert covered[0][0] == 0 and covered[-1][1] == width, "regions must cover the page"
    for (_, end), (start, _) in zip(covered, covered[1:]):
        assert end == start, "regions must not leave a gap that drops content"


# --------------------------------------------------------------------------
# code grammars
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "grammar,code,expected",
    [
        ("space_suffix", "MATH 21A", "MATH"),
        ("space_suffix", "COMS W1004", "COMS"),
        ("space_suffix", "CIV ENG 130N", "CIV ENG"),  # multi-word department
        ("space_suffix", "MS&E 245A", "MS&E"),
        ("space_suffix", "Ph 1b", "Ph"),
        ("mit_dotted", "6.036", "6"),
        ("mit_dotted", "21W.011", "21W"),
        ("mit_dotted", "8.THU", "8"),
        ("jhu_dotted", "AS.270.103", "270"),
    ],
)
def test_dept_extraction(grammar, code, expected):
    profile = Profile(id="t", institution="t", code_grammar=grammar)
    assert profile.dept_of(code) == expected


# --------------------------------------------------------------------------
# grading scales
# --------------------------------------------------------------------------


def test_grading_key_anchor_drives_normalisation():
    mit = parse_grading_key(["GRADING KEY", "A = 5.00 B = 4.00 C = 3.00 D = 2.00 F = 0.00"])
    assert mit.anchor == 5.0
    assert mit.factor == pytest.approx(0.8)  # 4.0 basis

    harvard = parse_grading_key(["GRADING KEY", "A = 4.00 A- = 3.67 B+ = 3.33 B = 3.00"])
    assert harvard.factor == pytest.approx(1.0)


def test_a_plus_above_four_survives_normalisation():
    scale = parse_grading_key(["GRADING KEY", "A+ = 4.33 A = 4.00 A- = 3.67"])
    assert scale.values["A+"] * scale.factor == pytest.approx(4.33)


# --------------------------------------------------------------------------
# category ruleset
# --------------------------------------------------------------------------


def _course(code, dept, title):
    return Course(title=title, credits=1.0, code=code, dept=dept, grade_raw="A")


def test_colliding_dept_tokens_respect_the_institution():
    stem = load_ruleset("STEM")
    # PS is Political Science at Caltech, not planetary science.
    assert not stem.classify(_course("PS 12", "PS", "Introduction to Political Science"), "caltech").in_category
    # ENG is English at Princeton, not engineering.
    assert not stem.classify(_course("ENG 200", "ENG", "Introduction to English Literature"), "princeton").in_category
    assert stem.classify(_course("MAT 103", "MAT", "Calculus I"), "princeton").in_category


def test_unknown_department_is_reported_not_swallowed():
    stem = load_ruleset("STEM")
    decision = stem.classify(_course("ZZZ 1", "ZZZ", "Something New"), "harvard_college")
    assert decision.unmapped is True


def test_title_audit_flags_a_suspect_mapping():
    stem = load_ruleset("STEM")
    # MATH is mapped STEM at Harvard, but this title reads humanities.
    decision = stem.classify(_course("MATH 1", "MATH", "History of Mathematics"), "harvard_college")
    assert decision.in_category is True  # dept map stays authoritative
    assert decision.disputed is True  # ...and the disagreement is surfaced


# --------------------------------------------------------------------------
# integration
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus():
    config = Config.load(CONFIG)
    if not config.pdf_folder.is_dir():
        pytest.skip(f"corpus not available: {config.pdf_folder}")
    profiles = load_profiles()
    ruleset = load_ruleset(config.categories[0])
    out = []
    for pdf in config.pdfs():
        transcript = parse_transcript(pdf, profiles)
        apply_categories(transcript, ruleset)
        out.append((transcript, summarise(transcript, ruleset.id)))
    return out


def test_every_transcript_passes_its_validation_gate(corpus):
    failed = [t.file for t, _ in corpus if t.gate == "FAIL"]
    assert not failed, f"validation gate failed for: {failed}"


def test_every_transcript_is_claimed_by_a_profile(corpus):
    generic = [t.file for t, _ in corpus if t.institution_id == "generic_us_term_block"]
    assert not generic, f"no profile matched: {generic}"


def test_title_disputes_reach_the_output(corpus):
    """A dept map the titles disagree with must be reported, not just counted.

    `disputed` was computed and discarded for a while, which meant a wrong
    mapping stayed invisible -- the opposite of what the ruleset promises.
    """
    disputes = [w for t, _ in corpus for w in t.warnings if "disputed by course title" in w]
    assert disputes, "expected the corpus to surface at least one title dispute"
    assert all("the map decides" in w for w in disputes), "dispute must say who won"


def test_sibling_institutions_are_not_confused_by_name_prefix(corpus):
    """Five Maryland schools share a name prefix; each must claim its own file.

    "UNIVERSITY OF MARYLAND" alone matched all five and handed four of them
    College Park's department map, and "...BALTIMORE" is in turn a prefix of
    "...BALTIMORE COUNTY".  Dispatch resolves these by score, so a fingerprint
    edit elsewhere could quietly re-break it.
    """
    expected = {
        "brian_perez.pdf": "umd",
        "nicole_cooper.pdf": "umbc",
        "dennis_cook.pdf": "md_common",
        "douglas_murphy.pdf": "md_common",
        "elizabeth_green.pdf": "md_common",
    }
    actual = {t.file: t.institution_id for t, _ in corpus if t.file in expected}
    assert actual == expected


def test_shared_profile_reads_each_school_name_from_the_page(corpus):
    """md_common serves many schools, so its name must come from the document."""
    family = [t for t, _ in corpus if t.institution_id == "md_common"]
    assert len(family) > 30, f"expected the whole family, got {len(family)}"
    names = {t.institution for t in family}
    assert len(names) == len(family), "each file should report its own school"
    assert "Maryland institution" not in names, "fallback label leaked into output"


def test_no_unmapped_departments_in_the_corpus(corpus):
    unmapped = [(t.file, w) for t, _ in corpus for w in t.warnings if "not in the" in w]
    assert not unmapped, f"ruleset needs extending: {unmapped}"


def test_mit_five_point_scale_is_normalised(corpus):
    mit = [r for t, r in corpus if t.institution_id == "mit"]
    assert mit, "expected MIT transcripts in the corpus"
    for result in mit:
        assert result.scale_anchor == 5.0
        # No MIT course exceeds 4.00 once rescaled, so capping is a no-op here
        # and the capped figure is exactly the rescaled one.
        assert result.gpa_capped == pytest.approx(result.gpa_raw * 0.8, abs=1e-3)
        assert result.gpa_capped < 4.0


def test_ranking_is_ordered_and_complete(corpus):
    ranked, held = rank([r for _, r in corpus])
    assert len(ranked) + len(held) == len(corpus)
    values = [r.gpa_capped for r in ranked]
    assert values == sorted(values, reverse=True)
    assert all(v <= 4.0 for v in values), "capped GPA must never exceed 4.00"
