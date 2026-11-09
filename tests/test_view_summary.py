"""summary (ABS, table, Transcript) and browser_view's View → Summary page."""

import json
import re
from pathlib import Path

import pytest

from browser_view.app import create_app
from summary import ABS, RankItem, table
from summary.names import letter_names, name_keys, upload_names
from summary.personal import Profile, profile_fields
from summary.recommendation import Recommendation
from summary.transcript import Transcript, source_hint


class Fixed(ABS):
    def __init__(self, title, items):
        self.title, self.items = title, items

    def rank(self):
        return [RankItem(n, o) for n, o in self.items]


def test_abs_cannot_be_used_without_rank():
    class NoRank(ABS):
        pass
    with pytest.raises(TypeError):
        NoRank()


def test_an_object_with_columns_gives_sub_columns_under_its_title():
    class Split(Fixed):
        def columns(self):
            return [("x", [RankItem("Ann", 1)]), ("y", [RankItem("bob", 2)])]
    from summary import groups
    columns = table([Fixed("A", [("Ann", 0)]), Split("S", [])], ["Ann", "bob"])
    assert [(c.group, c.title) for c in columns] == [("", "A"), ("S", "x"), ("S", "y")]
    assert columns[2].orders == {"Ann": None, "bob": 2}
    assert columns[1].folded == "x/y"  # no folded_title: the sub-titles joined
    # each group of sub-columns is followed by its folded cell, which takes an index
    assert [(g.title, [i for i, _ in g.cols], g.fold_index) for g in groups(columns)] == [
        ("", [1], None), ("S", [2, 3], 4)]
    columns = table([Split("S", []), Fixed("A", [])], ["Ann"])
    assert [(g.title, [i for i, _ in g.cols], g.fold_index) for g in groups(columns)] == [
        ("S", [1, 2], 3), ("", [4], None)]


def test_table_gives_every_union_name_an_order_or_none():
    names = ["Ann", "bob", "Cy"]
    columns = table([Fixed("A", [("bob", 1), ("Ann", 2), ("bob", 9), ("Zed", 5)]),
                     Fixed("", [("Cy", 3)])], names)
    assert [c.title for c in columns] == ["A", "Fixed"]
    # a repeated name keeps its first order; a name not in the union is dropped
    assert columns[0].orders == {"Ann": 2, "bob": 1, "Cy": None}
    assert columns[1].orders == {"Ann": None, "bob": None, "Cy": 3}


@pytest.fixture
def cfg(tmp_path):
    """config.yaml and its settings, plus where the test puts each kind of file:
    ``letters`` (PDFs, input), ``ranks`` (letter_ranks.csv) and ``personal`` (*.json)."""
    work = tmp_path / "work"
    (work / "jev").mkdir(parents=True)
    (work / "transcripts").mkdir()
    for stem, name in (("ann", "Ann Lee"), ("bob", "Bob Ray"), ("cy", "")):
        (work / "transcripts" / f"{stem}.common.json").write_text(json.dumps(
            {"source_file": f"{stem}.pdf", "student": {"name": name}}))
    (work / "transcripts" / "bad.common.json").write_text("{nope")
    (work / "jev" / "stem_gpa.csv").write_text(
        "student,STEM_GPA,file\nAnn Lee,3.5,ann.pdf\nGone,2.0,gone.pdf\n")
    (work / "jev" / "law_gpa.csv").write_text("student,LAW_GPA,file\nBob Ray,3.9,bob.pdf\n")
    letters, ranks, personal = tmp_path / "in" / "letters", work / "letters", work / "personal"
    for d in (letters, ranks, personal):
        d.mkdir(parents=True)
    for name in ("ann_letter1.pdf", "dee_lo_letter1.pdf", "eve_letter1.pdf"):
        (letters / name).write_bytes(b"%PDF")
    (personal / "ann.json").write_text(json.dumps({"name": "Ann Lee", "age": 20}))  # same as the transcript
    (personal / "eve.json").write_text(json.dumps({"name": "Eve Ng"}))
    (personal / "fay.json").write_text(json.dumps({"age": 3}))  # no name: not a row
    config = tmp_path / "config.yaml"
    config.write_text(f"input-folder: {tmp_path / 'in'}\nwork-folder: {work}\n"
                      "active-category-builder: jev\n")
    return {"input-folder": str(tmp_path / "in"), "work-folder": str(work), "config": config,
            "letters": str(letters), "ranks": str(ranks), "personal": str(personal)}


def test_upload_names_is_the_union_of_every_upload_type(cfg):
    # transcripts: Ann Lee, Bob Ray; profiles: Ann Lee, Eve Ng; letters: ann and eve
    # take those names, dee_lo has neither so is named from its key
    assert upload_names(cfg) == ["Ann Lee", "Bob Ray", "Dee Lo", "Eve Ng"]


def test_transcript_ranks_every_uploaded_transcript_by_its_category_gpa(cfg):
    t = Transcript(cfg, "jev", "stem")
    assert t.title == "Transcript" and len(t.transcripts) == 3  # bad JSON skipped
    # json student.name is the name; no GPA is None; a nameless transcript is left out
    assert t.rank() == [RankItem("Ann Lee", 3.5), RankItem("Bob Ray", None)]
    assert Transcript(cfg, "jev", "medical").rank() == [RankItem("Ann Lee", None),
                                                        RankItem("Bob Ray", None)]


def test_view_summary_page(cfg):
    client = create_app(cfg["config"]).test_client()
    body = client.get("/view/summary", query_string={"category": "stem"}).get_data(as_text=True)
    nav = body[body.index('<nav class="side"'):body.index("</nav>")]
    assert nav.index(">View</span>") < nav.index('href="/view/summary" class="sub active"')
    assert '<a href="/" class="home">Student Rank</a>' in body and ">Rank</a>" in nav
    # View (only the table) on the left, Config (the Transcript row) on the right
    panels = body[body.index('<div class="rank-panels">'):]
    view = panels[:panels.index('id="rank-panel-config"')]
    # no Builder / Category pickers here (they live in Config); only the All / Valid one
    assert 'id="rank-summary"' in view and view.count("<select") == 1 and 'name="category"' not in view
    config = panels[panels.index('id="rank-panel-config"'):panels.index("</section>\n</div>")]
    assert '<table class="rank-config">' in config
    assert "<th>Column</th><th>Filter</th>" in config and "Rank by" not in config
    assert '<h2 id="rank-config-heading">Config</h2>' in config
    assert '<main class="wide">' in body  # the page uses the whole window width
    assert 'id="transcript-action" aria-haspopup="dialog" title="The applicant&#39;s academic record: GPAs from their transcript&#39;s courses, for the STEM category">Transcript</button></td>' in config
    assert "<td>STEM GPA</td>" not in config  # Rank by is only in the dialog
    # its dialog: only the category of the active builder, the applied one selected
    dialog = body[body.index('<dialog id="transcript-dialog"'):body.index("</dialog>")]
    assert 'name="builder"' not in dialog and "Builder</label>" not in dialog
    assert '<option value="STEM" selected>STEM</option>' in dialog and '<option value="LAW" >' in dialog
    assert ">Apply</button>" in dialog and "<code>jev</code> builder" in dialog
    # Rank by: one radio per source, the applied one (by default the category GPA) checked
    assert '<input type="radio" name="by" value="category-gpa" checked>' in dialog
    assert 'value="all-courses-gpa" >' in dialog and 'value="category-pct" >' in dialog
    assert '<span data-source="category-pct" title="Share of the applicant&#39;s courses that are STEM courses (0–100%)">STEM CRS %</span>' in dialog
    # Transcript heads three sub-columns over a second header row; the others span both
    assert '<th rowspan="2" data-col="0" title="The applicant&#39;s name"><button type="button" class="sort">Name</button></th>' in body
    assert '<th colspan="3" class="group" scope="colgroup" data-group="Transcript" data-span="3"><button type="button" class="fold" aria-expanded="true" title="The applicant&#39;s academic record: GPAs from their transcript&#39;s courses, for the STEM category. Click to fold or unfold the Transcript columns">Transcript</button></th>' in body
    assert '<th class="num sub-col" data-type="num" data-col="1" title="GPA (0–4) of the applicant&#39;s STEM courses"><button type="button" class="sort">STEM GPA</button></th><th class="num sub-col" data-type="num" data-col="2" title="GPA (0–4) of all the applicant&#39;s courses"><button type="button" class="sort">ALL GPA</button></th><th class="num sub-col" data-type="num" data-col="3" title="Share of the applicant&#39;s courses that are STEM courses (0–100%)"><button type="button" class="sort">STEM CRS %</button></th>' in body
    assert '<th class="num fold-col" data-fold="4" data-group="Transcript" title="The applicant&#39;s academic record: GPAs from their transcript&#39;s courses, for the STEM category (STEM/ALL/%)" hidden>STEM/ALL/%</th>' in body
    # Recommend Letter too: Score and Count, then its folded Score/Count; Profile after
    assert ('<th colspan="2" class="group" scope="colgroup" data-group="Recommend Letter" data-span="2">'
            '<button type="button" class="fold" aria-expanded="true" title="The applicant&#39;s recommendation letters: how strongly they recommend, and how many. Click to fold or unfold the Recommend Letter columns">'
            'Recommend Letter</button></th>') in body
    assert ('<th class="num sub-col" data-type="num" data-col="5" title="How strongly the letters recommend, together: 100 all excellent, 90 excellent and good, 80 all good, 70 at least one good, 60 all normal"><button type="button" class="sort">Score</button></th>'
            '<th class="num sub-col" data-type="num" data-col="6" title="How many recommendation letters the applicant has"><button type="button" class="sort">Count</button></th>'
            '<th class="num fold-col" data-fold="7" data-group="Recommend Letter" title="The applicant&#39;s recommendation letters: how strongly they recommend, and how many (Score/Count)" hidden>Score/Count</th>') in body
    # Profile: no institution or program in these profiles, so their first field, age
    assert ('<th colspan="1" class="group" scope="colgroup" data-group="Profile" data-span="1">' in body)
    assert ('<th class="num sub-col" data-type="num" data-col="8" title="The applicant&#39;s age, in years"><button type="button" class="sort">age</button></th>'
            '<th class="num fold-col" data-fold="9" data-group="Profile" title="The applicant&#39;s personal information (age)" hidden>age</th>') in body
    assert "sortBy(1);" in body  # by the STEM GPA sub-column
    ranks = body[body.index('<table id="rank-summary">'):body.index("</table>", body.index('id="rank-summary"'))]
    assert ranks.split("<tbody>")[1].split("</tbody>")[0].count("<tr ") == 4  # one row per upload name
    assert '<h2 id="rank-view-heading">View</h2>' in body
    assert '<span class="heading-count" id="rank-count" aria-live="polite">4 rows</span>' in body
    # each name links to the student dialog by its file key
    for name, key in (("Ann Lee", "ann"), ("Bob Ray", "bob"), ("Dee Lo", "dee_lo"), ("Eve Ng", "eve")):
        assert f'<td><a href="#" class="student-link" data-file="{key}.pdf">{name}</a></td>' in ranks
    assert 'id="student-dialog"' in body and "studentRows(table.tBodies[0]" in body
    assert '{ builder: "jev", category: "STEM" }' in body
    assert "Gone" not in body  # only in the CSV, not uploaded
    assert '<td class="num sub-col" data-sort="3.5">3.50</td>' in body  # a float: two decimals
    # Transcript: Bob, Dee, Eve have no STEM GPA and nobody has the other two (not in
    # the CSV); Recommend Letter: no letter is ranked yet (Score), Bob has no letter
    # (Count); Profile age: Bob and Dee have no profile, Eve's has no age
    assert ranks.count('<td class="num sub-col" data-sort="">–</td>') == 3 + 4 + 4 + 4 + 1 + 3
    assert '<td class="num fold-col" hidden>–/1</td>' in ranks and '<td class="num fold-col" hidden>–/–</td>' in ranks
    assert '<td class="num fold-col" hidden>3.50/–/–</td>' in ranks
    assert '<td class="num sub-col" data-sort="20">20</td>' in ranks
    assert '<td class="num sub-col" data-sort="3.5">3.50</td>' in ranks
    # no category asked for: STEM
    assert ">STEM GPA</button>" in client.get("/view/summary").get_data(as_text=True)


@pytest.mark.parametrize("by, value, col", [
    ("category-gpa", "3.5", 1),
    ("all-courses-gpa", "3.2", 2),
    ("category-pct", "40.0", 3),
])
def test_transcript_ranks_by_the_chosen_source(cfg, by, value, col):
    (Path(cfg["work-folder"]) / "jev" / "stem_gpa.csv").write_text(
        "student,STEM_GPA,all_courses_GPA,stem_all_pct,file\nAnn Lee,3.5,3.2,40.0,ann.pdf\n")
    t = Transcript(cfg, "jev", "stem", by)
    assert t.title == "Transcript" and t.rank()[0] == RankItem("Ann Lee", float(value))
    # every source is a sub-column, whatever Rank by is
    assert t.columns() == [("STEM GPA", [RankItem("Ann Lee", 3.5), RankItem("Bob Ray", None)]),
                           ("ALL GPA", [RankItem("Ann Lee", 3.2), RankItem("Bob Ray", None)]),
                           ("STEM CRS %", [RankItem("Ann Lee", 40.0), RankItem("Bob Ray", None)])]
    body = create_app(cfg["config"]).test_client().get(
        "/view/summary", query_string={"category": "stem", "by": by}).get_data(as_text=True)
    for v in ("3.5", "3.2", "40.0"):
        assert f'data-sort="{v}">{float(v):.2f}</td>' in body
    # the folded cell, hidden until ▼: all three, no sort of its own
    assert '<td class="num fold-col" hidden>3.50/3.20/40.00</td>' in body
    assert '<td class="num fold-col" hidden>–/–/–</td>' in body  # Bob Ray
    assert ">STEM/ALL/%</button>" not in body
    assert 'localStorage.getItem(FOLD_KEY)' in body and "th button.fold[aria-expanded=false]::after" in body
    assert f'name="by" value="{by}" checked>' in body
    assert f"sortBy({col});" in body  # the page opens sorted by the Rank by sub-column


def test_transcript_rejects_an_unknown_source_and_the_page_falls_back(cfg):
    with pytest.raises(ValueError):
        Transcript(cfg, "jev", "stem", "bogus")
    body = create_app(cfg["config"]).test_client().get(
        "/view/summary", query_string={"category": "stem", "by": "bogus"}).get_data(as_text=True)
    assert 'name="by" value="category-gpa" checked>' in body and "sortBy(1);" in body


def test_name_keys_map_each_name_to_its_file_key(cfg):
    assert name_keys(cfg) == {"Ann Lee": "ann", "Bob Ray": "bob", "Dee Lo": "dee_lo", "Eve Ng": "eve"}
    assert list(name_keys(cfg)) == upload_names(cfg)


def test_letter_names_take_the_transcript_or_profile_name(cfg):
    assert letter_names(cfg) == {"ann": "Ann Lee", "dee_lo": "Dee Lo", "eve": "Eve Ng"}


@pytest.fixture
def lettered(cfg):
    """Ann: 2 letters, score 90; Eve: 1 letter, score 60; Dee: 1 letter, not ranked."""
    (Path(cfg["letters"]) / "ann_letter2.pdf").write_bytes(b"%PDF")
    (Path(cfg["ranks"]) / "letter_ranks.csv").write_text(
        "filename,rank,confidence\nann_letter1.txt,excellent,0.9\nann_letter2.txt,good,0.9\n"
        "eve_letter1.txt,normal,0.9\n")
    return cfg


def test_recommendation_gives_score_and_count(lettered):
    rec = Recommendation(lettered)
    assert rec.title == "Recommend Letter" and rec.folded_title == "Score/Count"
    assert sorted(rec.rank(), key=lambda i: i.name) == [  # rank(): the Score
        RankItem("Ann Lee", 90), RankItem("Dee Lo", None), RankItem("Eve Ng", 60)]
    (score, score_items), (cnt, count_items) = rec.columns()
    assert (score, cnt) == ("Score", "Count")
    assert {i.name: i.order for i in score_items} == {"Ann Lee": 90, "Dee Lo": None, "Eve Ng": 60}
    assert {i.name: i.order for i in count_items} == {"Ann Lee": 2, "Dee Lo": 1, "Eve Ng": 1}


@pytest.mark.parametrize("ranges, scores, counts", [
    (None, {"Ann Lee": 90, "Dee Lo": None, "Eve Ng": 60}, {"Ann Lee": 2, "Dee Lo": 1, "Eve Ng": 1}),
    # a Score range blanks only the Score cells outside it; Count stays
    ({"group-rank": (80, None)}, {"Ann Lee": 90, "Dee Lo": None, "Eve Ng": None},
     {"Ann Lee": 2, "Dee Lo": 1, "Eve Ng": 1}),
    ({"group-rank": (60, 90)}, {"Ann Lee": 90, "Dee Lo": None, "Eve Ng": 60},  # both ends included
     {"Ann Lee": 2, "Dee Lo": 1, "Eve Ng": 1}),
    # a Count range blanks only the Count cells
    ({"letters": (2, None)}, {"Ann Lee": 90, "Dee Lo": None, "Eve Ng": 60},
     {"Ann Lee": 2, "Dee Lo": None, "Eve Ng": None}),
    ({"group-rank": (50, 70), "letters": (2, 2)}, {"Ann Lee": None, "Dee Lo": None, "Eve Ng": 60},
     {"Ann Lee": 2, "Dee Lo": None, "Eve Ng": None}),
    ({"group-rank": (None, None)}, {"Ann Lee": 90, "Dee Lo": None, "Eve Ng": 60},  # no bound
     {"Ann Lee": 2, "Dee Lo": 1, "Eve Ng": 1}),
])
def test_recommendation_ranges_blank_only_their_own_sub_column(lettered, ranges, scores, counts):
    rec = Recommendation(lettered, ranges)
    (_, score_items), (_, count_items) = rec.columns()
    # every student stays
    assert {i.name: i.order for i in score_items} == scores
    assert {i.name: i.order for i in count_items} == counts
    assert {i.name: i.order for i in rec.rank()} == scores


@pytest.mark.parametrize("ranges", [{"group-rank": (90, 80)}, {"bogus": (1, 2)}])
def test_recommendation_rejects_a_reversed_range_or_an_unknown_source(cfg, ranges):
    with pytest.raises(ValueError):
        Recommendation(cfg, ranges)


def test_recommendation_range_labels():
    from summary.recommendation import range_label
    assert range_label(80, None) == "≥ 80" and range_label(None, 2.0) == "≤ 2"
    assert range_label(1, 3) == "1–3" and range_label(None, None) == ""


def test_view_summary_has_a_recommendation_column_and_dialog(cfg):
    (Path(cfg["ranks"]) / "letter_ranks.csv").write_text(
        "filename,rank,confidence\nann_letter1.txt,good,0.9\n")
    client = create_app(cfg["config"]).test_client()
    body = client.get("/view/summary", query_string={"category": "stem", "by": "category-pct"}).get_data(as_text=True)
    ranks = body[body.index('<table id="rank-summary">'):]
    assert ">Score</button>" in ranks and ">Count</button>" in ranks
    assert '<td class="num sub-col" data-sort="80">80</td>' in ranks  # an int: as it is
    assert '<td class="num fold-col" hidden>80/1</td>' in ranks
    config = body[body.index('<table class="rank-config">'):]
    # no Filter yet
    assert ('id="recommendation-action" aria-haspopup="dialog" title="The applicant&#39;s recommendation letters: how strongly they recommend, and how many">Recommend Letter</button></td>\n'
            '      \n      <td>–</td>') in config
    assert '<h2 id="recommendation-dialog-title">Recommend Letter</h2>' in body
    assert "sortBy(3);" in body  # the Transcript's Rank by (% STEM) still opens the sort
    rec = body[body.index('id="recommendation-form"'):]
    rec = rec[:rec.index("</form>")]
    # each dialog carries the other column's choice
    assert '<input type="hidden" name="category" value="STEM">' in rec
    assert '<input type="hidden" name="by" value="category-pct">' in rec
    # no Rank by: a Filter row each for Score (0-100) and Count (0 or more), empty
    assert 'name="rec_by"' not in body and "<legend>Rank by</legend>" not in rec
    assert '>Score</span>' in rec and '>Count</span>' in rec
    assert '<span title="How many recommendation letters the applicant has">Count</span>' in rec
    assert 'name="score_min" min="0" max="100" step="10"\n            value="" aria-label="Lowest Score"' in rec
    assert 'name="count_max" min="0" step="1"\n            value="" aria-label="Highest Count"' in rec


def test_view_summary_filters_by_the_recommend_letter_ranges(lettered):
    client = create_app(lettered["config"]).test_client()
    body = client.get("/view/summary", query_string={
        "category": "stem", "score_min": "80", "count_min": "2", "count_max": "9",
        "gpa_min": "3", "prof_show": "age"}).get_data(as_text=True)
    ranks = body[body.index('<table id="rank-summary">'):]
    assert '<td class="num fold-col" hidden>90/2</td>' in ranks  # Ann
    assert '60/1' not in ranks and '–/1' not in ranks  # Eve and Dee left out
    config = body[body.index('<table class="rank-config">'):]
    assert "<td>Score ≥ 80<br>Count 2–9</td>" in config
    rec = body[body.index('id="recommendation-form"'):]
    rec = rec[:rec.index("</form>")]
    assert 'value="80" aria-label="Lowest Score"' in rec and 'value="9" aria-label="Highest Count"' in rec
    assert '<input type="hidden" name="gpa_min" value="3.0">' in rec  # the Transcript's range
    # the other dialogs keep the Recommend Letter ranges
    for form in ("transcript-form", "profile-form"):
        part = body[body.index(f'id="{form}"'):]
        part = part[:part.index("</form>")]
        for hidden in ('name="score_min" value="80.0"', 'name="count_min" value="2.0"',
                       'name="count_max" value="9.0"'):
            assert f'<input type="hidden" {hidden}>' in part
        assert 'name="score_max"' not in part
    # a reversed range is swapped; a bad bound (not a number, below 0, Score above 100) is ignored
    body = client.get("/view/summary", query_string={"count_min": "3", "count_max": "1"}).get_data(as_text=True)
    assert "<td>Count 1–3</td>" in body
    body = client.get("/view/summary", query_string={
        "score_min": "x", "score_max": "120", "count_min": "-1"}).get_data(as_text=True)
    config = body[body.index('<table class="rank-config">'):]
    assert ">Recommend Letter</button></td>\n      \n      <td>–</td>" in config


def test_without_a_transcript_column_the_page_sorts_by_the_recommend_letter_score(cfg):
    for csv_file in (Path(cfg["work-folder"]) / "jev").glob("*_gpa.csv"):
        csv_file.unlink()
    body = create_app(cfg["config"]).test_client().get("/view/summary").get_data(as_text=True)
    assert 'data-group="Transcript"' not in body
    assert '<th class="num sub-col" data-type="num" data-col="1" title="How strongly the letters recommend, together: 100 all excellent, 90 excellent and good, 80 all good, 70 at least one good, 60 all normal"><button type="button" class="sort">Score</button></th>' in body
    assert "sortBy(1);" in body


def test_view_summary_without_results_says_so(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"work-folder: {tmp_path}\ninput-folder: {tmp_path / 'in'}\n")
    body = create_app(config).test_client().get("/view/summary").get_data(as_text=True)
    assert "has no results" in body
    assert '<button type="submit" disabled>Apply</button>' in body
    config.write_text(f"input-folder: {tmp_path / 'in'}\n")  # no work-folder
    body = create_app(config).test_client().get("/view/summary").get_data(as_text=True)
    assert 'class="error"' in body


def test_view_summary_all_valid_choice_marks_rows_with_every_column(cfg):
    (Path(cfg["ranks"]) / "letter_ranks.csv").write_text(
        "filename,rank,confidence\nann_letter1.txt,good,0.9\n")
    (Path(cfg["work-folder"]) / "jev" / "stem_gpa.csv").write_text(  # all three Transcript values
        "student,STEM_GPA,all_courses_GPA,stem_all_pct,file\nAnn Lee,3.5,3.2,40.0,ann.pdf\n")
    body = create_app(cfg["config"]).test_client().get(
        "/view/summary", query_string={"category": "stem"}).get_data(as_text=True)
    # a dropdown after the View heading and before the row count, All chosen
    head = body[body.index('<div class="rank-view-head">'):body.index('id="rank-summary"')]
    assert head.index(">View</h2>") < head.index('<select id="rank-show"') < head.index('id="rank-count"')
    assert '<option value="all" selected>All</option>' in head and '<option value="valid">Valid</option>' in head
    # the choice is remembered in this browser
    assert 'localStorage.getItem(KEY)' in body and 'localStorage.setItem(KEY, choice.value)' in body
    assert "data-show" not in head
    tbody = body.split('<table id="rank-summary">')[1].split("<tbody>")[1].split("</tbody>")[0]
    rows = tbody.split("</tr>")[:-1]
    valid = {r.split('class="student-link"')[1].split(">")[1].split("<")[0]: 'data-valid="yes"' in r
             for r in rows}
    # only Ann has the three Transcript values, a letter score (80) and an age (20)
    assert valid == {"Ann Lee": True, "Bob Ray": False, "Dee Lo": False, "Eve Ng": False}


@pytest.fixture
def profiled(cfg):
    personal = Path(cfg["personal"])
    (personal / "ann.json").write_text(json.dumps(
        {"name": "Ann Lee", "id_x": "a", "sex": "F", "age": 20, "birth_date": "2006-01-02",
         "major_residence": {"residence_country": "United States", "residence_state": "Maryland",
                             "residence_city": "Silver Spring"},
         "second_residence": None, "institution": "STATE U", "program": "Biology"}))
    (personal / "eve.json").write_text(json.dumps(
        {"name": "Eve Ng", "sex": "F", "age": 30, "birth_date": "1996-05-06",
         "major_residence": {"residence_country": "India", "residence_city": "Pune"},
         "institution": "OWN U", "program": "Law", "years_of_military_service": 2}))
    return cfg


def test_profile_fields_have_a_kind_and_the_values_of_a_list(profiled):
    fields = profile_fields(profiled)
    # every field but name, as View → Profiles shows them (no id_*; residences as M.*/S.*)
    assert list(fields) == ["sex", "age", "birth_date", "M.country", "M.state", "M.city",
                            "S.country", "S.state", "S.city", "institution", "program",
                            "years_of_military_service"]
    assert {n: f.kind for n, f in fields.items() if f.kind != "list"} == {
        "age": "number", "birth_date": "date", "years_of_military_service": "number"}
    assert fields["sex"].values == {"F": 2} and fields["sex"].blanks == 0
    assert fields["M.country"].values == {"India": 1, "United States": 1}
    assert fields["M.state"].values == {"Maryland": 1} and fields["M.state"].blanks == 1
    assert fields["S.city"].values == {} and fields["S.city"].blanks == 2
    assert fields["age"].values == {} and fields["years_of_military_service"].blanks == 1
    assert fields["M.state"].hint == "M = major residence: the state the applicant mainly lives in"
    assert fields["sex"].hint == "The applicant's sex"
    from summary.personal import Field
    assert Field("home.city", "list").hint == ""  # a field with no known meaning


def test_profile_shows_institution_and_program_by_default(profiled):
    p = Profile(profiled)
    assert p.title == "Profile" and p.folded_title == "institution/program"
    assert p.columns() == [("institution", [RankItem("Ann Lee", "STATE U"), RankItem("Eve Ng", "OWN U")]),
                           ("program", [RankItem("Ann Lee", "Biology"), RankItem("Eve Ng", "Law")])]
    # rank() is the first shown field; fay.json has no name
    assert Profile(profiled, ["age", "sex"]).rank() == [RankItem("Ann Lee", 20), RankItem("Eve Ng", 30)]
    with pytest.raises(ValueError):
        Profile(profiled, [])


@pytest.mark.parametrize("kwargs, field, want", [
    ({}, "age", {"Ann Lee": 20, "Eve Ng": 30}),
    # a range blanks only that field's values outside it
    ({"ranges": {"age": (25, None)}}, "age", {"Ann Lee": None, "Eve Ng": 30}),
    ({"ranges": {"age": (20, 30)}}, "age", {"Ann Lee": 20, "Eve Ng": 30}),  # both ends included
    ({"ranges": {"birth_date": ("2000-01-01", None)}}, "birth_date", {"Ann Lee": "2006-01-02", "Eve Ng": None}),
    ({"ranges": {"years_of_military_service": (1, 5)}}, "years_of_military_service", {"Ann Lee": None, "Eve Ng": 2}),
    # a list field: the values in the list, ignoring case
    ({"values": {"M.country": ["India"]}}, "M.country", {"Ann Lee": None, "Eve Ng": "India"}),
    ({"values": {"M.country": ["india", "UNITED STATES", "Mars"]}}, "M.country",
     {"Ann Lee": "United States", "Eve Ng": "India"}),
    ({"values": {"M.state": ["Maryland", "New York"]}}, "M.state", {"Ann Lee": "Maryland", "Eve Ng": None}),
    ({"values": {"M.country": []}}, "M.country", {"Ann Lee": "United States", "Eve Ng": "India"}),  # no list
    ({"values": {"M.country": None}}, "M.country", {"Ann Lee": "United States", "Eve Ng": "India"}),
    # a filter never blanks another field
    ({"values": {"sex": ["m"]}, "ranges": {"age": (25, None)}}, "program", {"Ann Lee": "Biology", "Eve Ng": "Law"}),
    ({"values": {"sex": ["m"]}}, "sex", {"Ann Lee": None, "Eve Ng": None}),
])
def test_profile_filters_blank_only_their_own_field(profiled, kwargs, field, want):
    shown = list(profile_fields(profiled))  # every field shown, so every filter applies
    p = Profile(profiled, shown, **kwargs)
    assert {i.name: i.order for i in dict(p.columns())[field]} == want


def test_profile_filters_only_by_a_shown_field(profiled):
    # age and sex are not shown (institution and program are): their filters are dropped
    p = Profile(profiled, ranges={"age": (25, None)}, values={"sex": ["M"], "program": ["Law"]})
    assert (p.ranges, p.values) == ({}, {"program": ["Law"]})
    assert p.labels() == ["program: Law"]
    # every profile stays; rank() is the first shown field, institution
    assert p.rank() == [RankItem("Ann Lee", "STATE U"), RankItem("Eve Ng", "OWN U")]
    assert dict(p.columns())["program"] == [RankItem("Ann Lee", None), RankItem("Eve Ng", "Law")]


@pytest.mark.parametrize("kwargs", [
    {"ranges": {"sex": (1, 2)}},  # not a range field
    {"ranges": {"age": (30, 20)}},
    {"ranges": {"birth_date": (1, None)}},  # a date field takes ISO dates
    {"ranges": {"age": ("2000-01-01", None)}},
    {"values": {"age": ["20"]}},  # a range field has no list
])
def test_profile_rejects_a_bad_filter(profiled, kwargs):
    with pytest.raises(ValueError):
        Profile(profiled, **kwargs)


def test_profile_filter_labels(profiled):
    p = Profile(profiled, ["age", "birth_date", "sex", "M.state"],
                ranges={"age": (20, None), "birth_date": ("2000-01-01", "2010-12-31")},
                values={"sex": ["M", "f"]})
    assert p.labels() == ["age ≥ 20", "birth_date 2000-01-01–2010-12-31", "sex: M, f"]


def test_view_summary_profile_columns_dialog_and_filters(profiled):
    client = create_app(profiled["config"]).test_client()
    body = client.get("/view/summary", query_string={"category": "stem"}).get_data(as_text=True)
    ranks = body[body.index('<table id="rank-summary">'):body.index("</table>", body.index('id="rank-summary"'))]
    # by default institution and program, text columns under Profile, foldable
    assert ('<th colspan="2" class="group" scope="colgroup" data-group="Profile" data-span="2">'
            '<button type="button" class="fold" aria-expanded="true" title="The applicant&#39;s personal information. Click to fold or unfold the Profile columns">'
            'Profile</button></th>') in ranks
    assert ('<th class="sub-col" data-col="8" title="The school the applicant&#39;s transcript is from"><button type="button" class="sort">institution</button></th>'
            '<th class="sub-col" data-col="9" title="The applicant&#39;s program of study (their major)"><button type="button" class="sort">program</button></th>'
            '<th class="fold-col" data-fold="10" data-group="Profile" title="The applicant&#39;s personal information (institution/program)" hidden>institution/program</th>') in ranks
    assert '<td class="sub-col">STATE U</td>' in ranks and '<td class="fold-col" hidden>STATE U/Biology</td>' in ranks
    config = body[body.index('<table class="rank-config">'):]
    assert 'id="profile-action" aria-haspopup="dialog" title="The applicant&#39;s personal information">Profile</button></td>\n      \n      <td>–</td>' in config
    dialog = body[body.index('<dialog id="profile-dialog"'):]
    dialog = dialog[:dialog.index("</dialog>")]
    assert "<th>Show</th><th>Field</th><th>Filter</th>" in dialog and "Rank by" not in dialog
    assert 'name="prof_show" value="institution" checked aria-label' in dialog
    assert 'name="prof_show" value="sex" aria-label' in dialog  # not shown
    # a range field: from/to (a date for birth_date), a list field: one box per value
    assert '<input type="number" name="prof_min.age" step="any" value=""' in dialog
    assert '<input type="date" name="prof_max.birth_date" value=""' in dialog
    assert ('<textarea name="prof_in.M.country" rows="2" placeholder="any; one value per line" '
            'aria-label="M.country values"></textarea>') in dialog
    assert 'name="prof_blank"' not in dialog and "keep blank" not in dialog  # nothing is dropped
    assert 'name="prof_min.sex"' not in dialog and 'name="prof_in.age"' not in dialog
    # M./S. names explain themselves on hover
    assert '<th scope="row" title="M = major residence: the state the applicant mainly lives in">M.state</th>' in dialog
    assert '<input type="hidden" name="category" value="STEM">' in dialog

    body = client.get("/view/summary", query_string=[
        ("category", "stem"), ("prof_show", "M.state"), ("prof_show", "age"), ("prof_show", "nope"),
        ("prof_show", "M.country"),
        ("prof_min.age", "25"), ("prof_max.age", "x"), ("prof_in.M.country", "india\n\n Mars \nINDIA"),
        ("prof_in.M.country", "Peru"), ("prof_blank", "M.state")]).get_data(as_text=True)
    ranks = body[body.index('<table id="rank-summary">'):body.index("</table>", body.index('id="rank-summary"'))]
    # in the asked order; M.state explains itself on hover
    assert ('data-col="8" title="M = major residence: the state the applicant mainly lives in">'
            '<button type="button" class="sort">M.state</button></th>') in ranks
    assert 'data-col="9" title="The applicant&#39;s age, in years"><button type="button" class="sort">age</button>' in ranks
    assert '>age</button>' in ranks and ">nope<" not in ranks and ">institution<" not in ranks
    assert '<td class="num sub-col" data-sort="30">30</td>' in ranks and 'data-sort="20"' not in ranks
    config = body[body.index('<table class="rank-config">'):]
    # one value per line, blank lines and repeats (ignoring case) dropped; M.state has no range
    assert "<td>age ≥ 25<br>M.country: india, Mars, Peru</td>" in config
    dialog = body[body.index('<dialog id="profile-dialog"'):]
    dialog = dialog[:dialog.index("</dialog>")]
    assert 'name="prof_min.age" step="any" value="25"' in dialog
    assert 'aria-label="M.country values">india\nMars\nPeru</textarea>' in dialog
    # the other dialogs keep the Profile choice, in order
    for form in ("transcript-form", "recommendation-form"):
        part = body[body.index(f'id="{form}"'):]
        part = part[:part.index("</form>")]
        hidden = re.findall(r'<input type="hidden" name="(prof_[^"]+)" value="([^"]*)">', part)
        assert hidden == [("prof_show", "M.state"), ("prof_show", "age"), ("prof_show", "M.country"),
                          ("prof_min.age", "25"),
                          ("prof_in.M.country", "india"), ("prof_in.M.country", "Mars"),
                          ("prof_in.M.country", "Peru")]
    # a reversed range is swapped, a bad date ignored
    body = client.get("/view/summary", query_string={
        "prof_show": "age", "prof_min.age": "40", "prof_max.age": "10",
        "prof_min.birth_date": "2000-13"}).get_data(as_text=True)
    assert "<td>age 10–40</td>" in body
    # a filter on a field that is not shown is not applied
    body = client.get("/view/summary", query_string={"prof_min.age": "40"}).get_data(as_text=True)
    assert "age ≥" not in body and ">Profile</button></td>\n      \n      <td>–</td>" in body
    # the dialog turns a row's filter off while its field is not shown
    assert "input.disabled = !box.checked;" in body and "Tick Show to filter by this field" in body


def test_view_summary_defaults_to_stem_and_institution_and_program(cfg):
    client = create_app(cfg["config"]).test_client()
    (Path(cfg["personal"]) / "eve.json").write_text(
        json.dumps({"name": "Eve Ng", "program": "Biology", "age": 30}))
    body = client.get("/view/summary").get_data(as_text=True)
    assert ">STEM GPA</button>" in body and '<option value="STEM" selected>STEM</option>' in body
    # only the default fields the profiles have: program (no institution)
    assert ">program</button>" in body and ">institution</button>" not in body and "<td class=\"sub-col\">Biology</td>" in body
    # without a STEM result, the first category; without either default field, the first field
    (Path(cfg["work-folder"]) / "jev" / "stem_gpa.csv").unlink()
    (Path(cfg["personal"]) / "eve.json").write_text(json.dumps({"name": "Eve Ng"}))
    body = client.get("/view/summary").get_data(as_text=True)
    assert ">LAW GPA</button>" in body and ">LAW CRS %</button>" in body
    assert ">age</button>" in body


def test_view_summary_each_column_applies_only_its_own_filter(profiled):
    """View → All lists every name; a filter blanks (–) only its own column's
    cells, the other columns keep their values; Valid needs a value in every one."""
    client = create_app(profiled["config"]).test_client()
    every = ["Ann Lee", "Bob Ray", "Dee Lo", "Eve Ng"]
    def rows(**query):
        """{name: (STEM GPA, letter Count, institution)} for every row, and the Valid names"""
        body = client.get("/view/summary", query_string={"category": "stem", **query}).get_data(as_text=True)
        tbody = body.split('<table id="rank-summary">')[1].split("<tbody>")[1].split("</tbody>")[0]
        table, valid = {}, []
        for row in tbody.split("</tr>")[:-1]:
            name = re.search(r'class="student-link" data-file="[^"]+">([^<]+)<', row).group(1)
            cells = re.findall(r"<td[^>]*>([^<]*)</td>", row)  # after Name: STEM GPA, ALL, %, fold, Score, Count, fold, ...
            table[name] = (cells[0], cells[5], cells[7])
            if 'data-valid="yes"' in row:
                valid.append(name)
        return table, valid
    plain, _ = rows()
    assert list(plain) == every
    assert plain["Ann Lee"] == ("3.50", "1", "STATE U") and plain["Eve Ng"] == ("–", "1", "OWN U")
    # a Profile filter: every name stays; only the Profile cells of the others go blank
    table, _ = rows(**{"prof_in.institution": "own u"})
    assert list(table) == every
    assert table["Ann Lee"] == ("3.50", "1", "–")  # her GPA and letters stay
    assert table["Eve Ng"] == ("–", "1", "OWN U")
    # a Transcript filter: only the Transcript cells of the others go blank
    table, _ = rows(gpa_min="3.6")
    assert table["Ann Lee"] == ("–", "1", "STATE U")
    # a filter on a field that is not shown is no filter
    assert rows(**{"prof_min.age": "25"})[0] == plain


@pytest.fixture
def ranged(cfg):
    (Path(cfg["work-folder"]) / "jev" / "stem_gpa.csv").write_text(
        "student,STEM_GPA,all_courses_GPA,stem_all_pct,file\n"
        "Ann Lee,3.5,3.2,40.0,ann.pdf\nBob Ray,2.0,3.6,10.0,bob.pdf\n")
    return cfg


@pytest.mark.parametrize("ranges, ann, bob", [
    (None, (3.5, 3.2, 40.0), (2.0, 3.6, 10.0)),
    # a STEM GPA range blanks only the STEM GPA cells outside it
    ({"category-gpa": (3.0, None)}, (3.5, 3.2, 40.0), (None, 3.6, 10.0)),
    ({"category-gpa": (2.0, 3.5)}, (3.5, 3.2, 40.0), (2.0, 3.6, 10.0)),  # both ends included
    ({"category-gpa": (2.1, 3.4)}, (None, 3.2, 40.0), (None, 3.6, 10.0)),
    ({"all-courses-gpa": (3.5, None)}, (3.5, None, 40.0), (2.0, 3.6, 10.0)),
    ({"category-pct": (20, 100)}, (3.5, 3.2, 40.0), (2.0, 3.6, None)),
    ({"category-pct": (None, None)}, (3.5, 3.2, 40.0), (2.0, 3.6, 10.0)),  # no bound: no filter
    # each range blanks its own sub-column only
    ({"all-courses-gpa": (3.0, None), "category-pct": (None, 20)}, (3.5, 3.2, None), (2.0, 3.6, 10.0)),
])
def test_transcript_ranges_blank_only_their_own_sub_column(ranged, ranges, ann, bob):
    t = Transcript(ranged, "jev", "stem", ranges=ranges)
    columns = t.columns()
    for name, want in (("Ann Lee", ann), ("Bob Ray", bob)):
        assert tuple({i.name: i.order for i in items}[name] for _, items in columns) == want
    assert [i.name for i in t.rank()] == ["Ann Lee", "Bob Ray"]  # every transcript stays


def test_transcript_rank_is_its_by_value_blank_outside_its_range(ranged):
    t = Transcript(ranged, "jev", "stem", "category-pct", {"all-courses-gpa": (3.5, 4)})
    assert t.rank() == [RankItem("Ann Lee", 40.0), RankItem("Bob Ray", 10.0)]  # % is not ranged
    t = Transcript(ranged, "jev", "stem", "category-pct", {"category-pct": (20, None)})
    assert t.rank() == [RankItem("Ann Lee", 40.0), RankItem("Bob Ray", None)]


@pytest.mark.parametrize("ranges", [{"category-gpa": (3.0, 2.0)}, {"bogus": (1, 2)}])
def test_transcript_rejects_a_reversed_range_or_an_unknown_source(cfg, ranges):
    with pytest.raises(ValueError):
        Transcript(cfg, "jev", "stem", ranges=ranges)


def test_range_labels():
    from summary.transcript import range_label
    assert range_label("category-gpa", 3, None) == "≥ 3.00"
    assert range_label("all-courses-gpa", None, 3.5) == "≤ 3.50"
    assert range_label("category-pct", 20, 50) == "20–50%"
    assert range_label("category-pct", None, None) == ""


def test_view_summary_page_filters_by_the_ranges(ranged):
    client = create_app(ranged["config"]).test_client()
    # no letter is ranked yet: nobody has a Score of 60 or more, so the Recommend
    # Letter cells are blank, but Ann keeps her GPA; nobody is Valid
    body = client.get("/view/summary", query_string={
        "category": "stem", "gpa_min": "3", "score_min": "60"}).get_data(as_text=True)
    assert 'data-sort="3.5">3.50</td>' in body and 'data-valid="yes"' not in body
    body = client.get("/view/summary", query_string={
        "category": "stem", "gpa_min": "3"}).get_data(as_text=True)
    ranks = body[body.index('<table id="rank-summary">'):]
    # Bob stays, only his STEM GPA (2.0) is blank; his ALL GPA stays
    assert 'data-sort="3.5">3.50</td>' in ranks and 'data-sort="2.0">' not in ranks
    assert '<td class="num fold-col" hidden>–/3.60/10.00</td>' in ranks
    config = body[body.index('<table class="rank-config">'):]
    assert "<th>Column</th><th>Filter</th>" in config
    assert "Transcript</button></td>\n      \n      <td>STEM GPA ≥ 3.00</td>" in config
    dialog = body[body.index('<dialog id="transcript-dialog"'):body.index("</dialog>")]
    # one row per source, labelled for the category, the applied range filled in
    for source, label, prefix, top in (("category-gpa", "STEM GPA", "gpa", 4),
                                       ("all-courses-gpa", "ALL GPA", "all_gpa", 4),
                                       ("category-pct", "STEM CRS %", "pct", 100)):
        assert f'<span data-source="{source}" title="{source_hint(source, "STEM").replace(chr(39), "&#39;")}">{label}</span>\n          <label>from' in dialog
        assert f'name="{prefix}_min" min="0" max="{top}"' in dialog
        assert f'name="{prefix}_max" min="0" max="{top}"' in dialog
    assert 'value="3.0" aria-label="Lowest category-gpa"' in dialog
    assert 'value="" aria-label="Lowest all-courses-gpa"' in dialog
    # the other dialogs keep the ranges
    rec = body[body.index('<dialog id="recommendation-dialog"'):]
    rec = rec[:rec.index("</dialog>")]
    assert '<input type="hidden" name="gpa_min" value="3.0">' in rec
    assert 'name="gpa_max"' not in rec and 'name="pct_min"' not in rec

    # all three at once: one Filter line each
    body = client.get("/view/summary", query_string={
        "category": "stem", "by": "category-pct", "gpa_max": "3.6", "all_gpa_min": "3.5",
        "pct_min": "5", "pct_max": "50"}).get_data(as_text=True)
    assert "<td>STEM GPA ≤ 3.60<br>ALL GPA ≥ 3.50<br>STEM CRS % 5–50%</td>" in body
    # each range blanks only its own sub-column: Ann's ALL GPA (3.2) is below 3.5
    assert '<td class="num fold-col" hidden>3.50/–/40.00</td>' in body
    assert '<td class="num fold-col" hidden>2.00/3.60/10.00</td>' in body
    prof = body[body.index('<dialog id="profile-dialog"'):]
    for hidden in ('name="gpa_max" value="3.6"', 'name="all_gpa_min" value="3.5"',
                   'name="pct_min" value="5.0"', 'name="pct_max" value="50.0"'):
        assert f'<input type="hidden" {hidden}>' in prof

    # a reversed range is swapped; a bound out of range or not a number is ignored
    body = client.get("/view/summary", query_string={
        "category": "stem", "gpa_min": "3.6", "gpa_max": "3"}).get_data(as_text=True)
    assert "<td>STEM GPA 3.00–3.60</td>" in body and 'data-sort="3.5">3.50</td>' in body
    body = client.get("/view/summary", query_string={
        "category": "stem", "gpa_min": "9", "all_gpa_max": "x", "pct_min": "101"}).get_data(as_text=True)
    config = body[body.index('<table class="rank-config">'):]
    assert "Transcript</button></td>\n      \n      <td>–</td>" in config
    assert 'data-sort="2.0">2.00</td>' in body


def test_every_sortable_header_points_at_its_body_cell(cfg):
    """_sort_table.html sorts row.cells[data-col]; a folded column has no data-col,
    so the indices skip it and must still name the right cell."""
    import re
    (Path(cfg["ranks"]) / "letter_ranks.csv").write_text(
        "filename,rank,confidence\nann_letter1.txt,good,0.9\n")
    body = create_app(cfg["config"]).test_client().get(
        "/view/summary", query_string={"category": "stem"}).get_data(as_text=True)
    head = body[body.index("<thead>"):body.index("</thead>")]
    cols = {int(i): t for i, t in re.findall(r'data-col="(\d+)"(?: title="[^"]*")?><button type="button" class="sort">([^<]+)<', head)}
    assert sorted(cols) == [0, 1, 2, 3, 5, 6, 8]  # 4, 7 and 9 are the folded cells
    assert 'data-fold="4"' in head and 'data-fold="7"' in head and 'data-fold="9"' in head
    tbody = body[body.index("<tbody>"):body.index("</tbody>")]
    for row in tbody.split("</tr>")[:-1]:
        cells = re.findall(r"<td[^>]*>", row)
        assert len(cells) == 10
        for i in cols:  # a sortable header's cell is never a folded one
            assert "fold-col" not in cells[i]
        assert all("fold-col" in cells[i] for i in (4, 7, 9))
    assert "headers.get(index)" in body  # sortBy looks a header up by its data-col


def test_profile_sex_filter_is_m_and_f_unless_a_list_is_given(profiled):
    client = create_app(profiled["config"]).test_client()
    def page(query):
        return client.get("/view/summary", query_string=[("category", "stem"), *query]).get_data(as_text=True)
    # the dialog starts with M and F, also while sex is not shown
    body = page([])
    assert 'aria-label="sex values">M\nF</textarea>' in body
    assert "sex: M, F" not in body  # not shown: no filter
    # shown with no list: M and F
    body = page([("prof_show", "sex")])
    assert "<td>sex: M, F</td>" in body
    # an edited list wins: only F; Ann and Eve are both F
    body = page([("prof_show", "sex"), ("prof_in.sex", "F")])
    assert "<td>sex: F</td>" in body and 'aria-label="sex values">F</textarea>' in body
    body = page([("prof_show", "sex"), ("prof_in.sex", "M")])
    tbody = body.split('<table id="rank-summary">')[1].split("<tbody>")[1].split("</tbody>")[0]
    assert ">F</td>" not in tbody  # every F is outside the list: blank
