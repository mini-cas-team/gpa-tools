"""letter_builder and browser_view's Recommendations pages; Jev and pdftotext are faked."""

import io
import time

import pytest

from browser_view.app import create_app
from category_builder.common import RunError
from jev_category.client import JevError
from letter_builder import RANKS_CSV, letters_folder, letters_work_folder, pdf2txt, post_rank, rank_letters


class FakeJev:
    """Stands in for JevClient: a letter's text says its rank; "boom" fails."""

    model = "jev-test"

    def __init__(self):
        self.asked = []

    def answers(self, state, questions):
        letter = state["letter"]
        self.asked.append(letter)
        if "boom" in letter:
            raise JevError("HTTP 500: down")
        assert questions["rank"]["type"] == "choice"
        choice = next(c for c in rank_letters.CRITERIA if c in letter)
        return {"rank": {"type": "choice", "choice": choice, "confidence": 0.8}}


def fake_pdftotext(pdf, work):
    if b"broken" in pdf.read_bytes():
        raise ValueError("Syntax Error: not a PDF")
    work.mkdir(parents=True, exist_ok=True)
    txt = work / f"{pdf.stem}.txt"
    txt.write_bytes(pdf.read_bytes())  # the "PDF" is its own text in these tests
    return txt


@pytest.fixture
def jev(monkeypatch):
    fake = FakeJev()
    monkeypatch.setattr(rank_letters, "jev_client", lambda raw: fake)
    monkeypatch.setattr(pdf2txt, "convert_one", fake_pdftotext)
    return fake


CONFIG = "input-folder: {tmp}/in\nwork-folder: {tmp}/work\n"


@pytest.fixture
def letters(tmp_path):
    """(<input-folder>/letters with ann_letter1.pdf, config.yaml, <work-folder>/letters)."""
    folder, work = tmp_path / "in" / "letters", tmp_path / "work" / "letters"
    folder.mkdir(parents=True)
    work.mkdir(parents=True)
    (folder / "ann_letter1.pdf").write_bytes(b"an excellent letter")
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(tmp=tmp_path))
    return folder, config, work


@pytest.fixture
def client(letters):
    return create_app(letters[1]).test_client()


def wait_for_letters(client):
    for _ in range(100):
        state = client.get("/letters/status").get_json()
        if state["status"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("letter run did not finish")


# --- letter_builder ---------------------------------------------------------------

def test_letter_folders_come_from_input_and_work_folder():
    cfg = {"input-folder": "/x", "work-folder": "/x/work"}
    assert letters_folder(cfg).as_posix() == "/x/letters"
    assert letters_work_folder(cfg).as_posix() == "/x/work/letters"
    for cfg in ({}, {"input-folder": "/x", "work-folder": "/w", "components": ["transcripts"]}):
        with pytest.raises(KeyError):
            letters_folder(cfg)


def test_pdf2txt_writes_the_txt_into_the_work_folder(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pdf2txt, "pdf_to_txt", lambda pdf, txt: calls.append((pdf, txt)))
    work = tmp_path / "work" / "letters"
    assert pdf2txt.convert_one(tmp_path / "ann_letter1.pdf", work) == work / "ann_letter1.txt"
    assert calls == [(tmp_path / "ann_letter1.pdf", work / "ann_letter1.txt")] and work.is_dir()


def test_rank_asks_one_choice_question():
    fake = FakeJev()
    assert rank_letters.rank(fake, "a good letter") == ("good", 0.8)


def test_rank_rejects_an_unknown_choice():
    class Odd(FakeJev):
        def answers(self, state, questions):
            return {"rank": {"choice": "superb"}}

    with pytest.raises(JevError):
        rank_letters.rank(Odd(), "text")


def test_jev_client_needs_the_api_key(monkeypatch):
    monkeypatch.delenv("GPA_TOOLS_KEY", raising=False)
    with pytest.raises(RunError, match="GPA_TOOLS_KEY"):
        rank_letters.jev_client({})


def test_merge_replaces_rows_by_filename(tmp_path):
    path = tmp_path / RANKS_CSV
    rank_letters.write_ranks(path, [("a.txt", "good", 0.5), ("b.txt", "normal", 0.4)])
    rank_letters.merge_ranks(path, [("b.txt", "excellent", 0.9), ("c.txt", "good", 0.7)])
    rows = rank_letters.read_ranks(path)
    assert list(rows) == ["a.txt", "b.txt", "c.txt"]
    assert rows["b.txt"]["rank"] == "excellent" and rows["b.txt"]["confidence"] == "0.900"


@pytest.mark.parametrize("rank, confidence, adjusted", [
    ("good", 0.69, "normal"), ("good", 0.7, "good"),
    ("excellent", 0.39, "normal"), ("excellent", 0.4, "good"), ("excellent", 0.69, "good"),
    ("excellent", 0.7, "excellent"), ("normal", 0.1, "normal"), ("good", None, "normal"),
])
def test_post_rank_rules(rank, confidence, adjusted):
    assert post_rank.adjust(rank, confidence) == adjusted


def test_post_rank_keeps_jevs_rank_and_leaves_the_csv_alone(tmp_path):
    path = tmp_path / RANKS_CSV
    rank_letters.write_ranks(path, [("a.txt", "excellent", 0.5), ("b.txt", "good", 0.9)])
    before = path.read_text()
    rows = post_rank.post_rank(path)
    assert [(r["filename"], r["jev_rank"], r["rank"]) for r in rows] == [
        ("a.txt", "excellent", "good"), ("b.txt", "good", "good")]
    assert path.read_text() == before


# --- pages ------------------------------------------------------------------------

def test_left_bar_has_the_recommendations_items(client):
    body = client.get("/letters/ftp-upload").get_data(as_text=True)
    assert 'href="/letters/ftp-upload" class="sub active" aria-current="page">Recommendations' in body
    assert 'href="/letters/view" class="sub">Recommendations' in body
    view = client.get("/letters/view").get_data(as_text=True)
    assert 'href="/letters/view" class="sub active" aria-current="page">Recommendations' in view


def test_upload_saves_converts_and_ranks_in_the_background(client, jev, letters):
    folder, _, work = letters
    files = [(io.BytesIO(b"a good letter"), "../bob_letter1.pdf"),
             (io.BytesIO(b"a normal letter"), "cy_letter1.pdf")]
    r = client.post("/letters/upload", data={"pdfs": files}, content_type="multipart/form-data")
    assert r.status_code == 200
    state = wait_for_letters(client)
    assert state["status"] == "done" and state["ranked"] == 2 and state["total"] == 2
    assert (folder / "bob_letter1.pdf").is_file() and (work / "bob_letter1.txt").is_file()
    assert not list(folder.glob("*.txt")) and not (folder / RANKS_CSV).exists()  # input stays input
    rows = rank_letters.read_ranks(work / RANKS_CSV)
    assert {n: r["rank"] for n, r in rows.items()} == {"bob_letter1.txt": "good",
                                                      "cy_letter1.txt": "normal"}


def test_upload_without_files_ranks_the_whole_folder_and_keeps_old_ranks(client, jev, letters):
    folder, _, work = letters
    rank_letters.write_ranks(work / RANKS_CSV, [("old.txt", "good", 0.6)])
    client.post("/letters/upload")
    assert wait_for_letters(client)["ranked"] == 1
    rows = rank_letters.read_ranks(work / RANKS_CSV)
    assert rows["ann_letter1.txt"]["rank"] == "excellent" and rows["old.txt"]["rank"] == "good"


def test_letters_with_a_txt_are_skipped_not_replaced(client, jev, letters):
    folder, _, work = letters
    (work / "ann_letter1.txt").write_text("an excellent letter")
    files = [(io.BytesIO(b"a good letter, again"), "ann_letter1.pdf"),
             (io.BytesIO(b"a normal letter"), "dan_letter1.pdf")]
    client.post("/letters/upload", data={"pdfs": files}, content_type="multipart/form-data")
    state = wait_for_letters(client)
    assert state["ranked"] == 1 and state["skipped"] == ["ann_letter1.pdf"]
    assert (folder / "ann_letter1.pdf").read_bytes() == b"an excellent letter"
    assert list(rank_letters.read_ranks(work / RANKS_CSV)) == ["dan_letter1.txt"]
    assert jev.asked == ["a normal letter"]


def test_nothing_new_starts_no_run(client, jev, letters):
    folder, _, work = letters
    (work / "ann_letter1.txt").write_text("an excellent letter")
    r = client.post("/letters/upload")
    assert r.get_json() == {"status": "skipped", "skipped": ["ann_letter1.pdf"]}
    assert client.get("/letters/status").get_json() == {"status": "idle"} and jev.asked == []


def test_failed_letters_are_listed_and_the_rest_ranked(client, jev, letters):
    files = [(io.BytesIO(b"broken"), "x.pdf"), (io.BytesIO(b"boom"), "y.pdf"),
             (io.BytesIO(b"a good letter"), "z.pdf")]
    client.post("/letters/upload", data={"pdfs": files}, content_type="multipart/form-data")
    state = wait_for_letters(client)
    assert state["status"] == "done" and state["ranked"] == 1
    assert [f["file"] for f in state["failures"]] == ["x.pdf", "y.pdf"]


def test_upload_errors(client, jev, letters, monkeypatch):
    r = client.post("/letters/upload", data={"pdfs": [(io.BytesIO(b"x"), "notes.txt")]},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "not a PDF" in r.get_json()["error"]
    (letters[0] / "ann_letter1.pdf").unlink()
    r = client.post("/letters/upload")
    assert r.status_code == 400 and "no PDFs" in r.get_json()["error"]

    def no_key(raw):
        raise RunError("The TypeSafe API key is not set: export GPA_TOOLS_KEY=<key>")
    monkeypatch.setattr(rank_letters, "jev_client", no_key)
    r = client.post("/letters/upload")
    assert r.status_code == 400 and "GPA_TOOLS_KEY" in r.get_json()["error"]


def test_view_lists_one_row_per_student_with_a_group_rank(client, letters, tmp_path):
    folder, config, work = letters
    pdfs = tmp_path / "in" / "transcripts"
    pdfs.mkdir()
    (pdfs / "ann.pdf").write_bytes(b"%PDF ann")
    (pdfs / "cy.pdf").write_bytes(b"%PDF cy")  # no letters: not listed
    (folder / "ann_letter2.pdf").write_bytes(b"a good letter")
    (folder / "eve_letter1.pdf").write_bytes(b"an excellent letter")  # no transcript
    rank_letters.write_ranks(work / RANKS_CSV, [("ann_letter1.txt", "excellent", 0.91),
                                                  ("ann_letter2.txt", "good", 0.8),
                                                  ("eve_letter1.txt", "excellent", 0.55)])
    body = client.get("/letters/view").get_data(as_text=True)
    rows = body.split("<tbody>")[1].split("</tbody>")[0].split("</tr>")[:-1]
    assert len(rows) == 2
    ann, eve = rows
    assert 'data-rank="90"' in ann and 'data-file="ann.pdf"' in ann
    assert 'data-category="">Ann</a>' in ann and 'data-sort="2">2</td>' in ann  # no builds yet
    # post_rank: excellent at 0.55 -> good; one good letter -> 80 (very good)
    assert 'data-rank="80"' in eve and 'data-file="eve.pdf"' in eve
    assert 'data-category="">Eve</a>' in eve and 'data-sort="1">1</td>' in eve
    assert 'id="student-dialog"' in body


def test_student_detail_without_a_category_has_pdf_and_letters(client, letters, tmp_path):
    folder, config, work = letters
    pdfs = tmp_path / "in" / "transcripts"
    pdfs.mkdir()
    (pdfs / "ann.pdf").write_bytes(b"%PDF ann")
    rank_letters.write_ranks(work / RANKS_CSV, [("ann_letter1.txt", "excellent", 0.91)])
    data = client.get("/transcripts/view/student", query_string={"file": "ann.pdf"}).get_json()
    assert data["student"] == "Ann" and data["courses"] is None
    assert data["pdf_url"] == "/transcripts/view/pdf?file=ann.pdf" and data["letter_rank"] == 100
    assert [letter["file"] for letter in data["letters"]] == ["ann_letter1.pdf"]
    eve = client.get("/transcripts/view/student", query_string={"file": "eve.pdf"}).get_json()
    assert eve["pdf_url"] is None and eve["letters"] == []
    assert client.get("/transcripts/view/student", query_string={"file": "../x"}).status_code == 404


def test_letter_pdfs_are_served_only_from_the_letters_folder(client, letters):
    assert client.get("/letters/pdf", query_string={"file": "ann_letter1.pdf"}).status_code == 200
    for bad in ("../config.yaml", "gone.pdf", "ann_letter1.txt", ""):
        assert client.get("/letters/pdf", query_string={"file": bad}).status_code == 404


def test_view_explains_when_there_are_no_letters(client, letters):
    (letters[0] / "ann_letter1.pdf").unlink()
    body = client.get("/letters/view").get_data(as_text=True)
    assert "no recommendation letters" in body and 'id="letter-students"' not in body


# --- student records ----------------------------------------------------------------

def test_student_info_gives_one_record_per_student():
    from letter_builder.students import student_info

    def row(rank, jev_rank, confidence):
        return {"rank": rank, "jev_rank": jev_rank, "confidence": confidence}

    ranks = {"adam_flores_letter1": row("good", "excellent", 0.5),
             "adam_flores_letter2": row("excellent", "excellent", 0.9),
             "bob_ray_letter1": row("good", "good", 0.8)}
    records = student_info(
        ["/t/bob_ray.pdf", "adam_flores.pdf", "cy_lee.pdf"],
        ["adam_flores_letter2.pdf", "/l/adam_flores_letter1.pdf", "bob_ray_letter1.pdf",
         "dan_wu_letter1.pdf"], ranks)
    assert records == [
        {"student": "adam_flores", "transcript": "adam_flores.pdf",
         "letters": [{"file": "adam_flores_letter1.pdf", **row("good", "excellent", 0.5)},
                     {"file": "adam_flores_letter2.pdf", **row("excellent", "excellent", 0.9)}],
         "group_rank": 90},
        {"student": "bob_ray", "transcript": "bob_ray.pdf",
         "letters": [{"file": "bob_ray_letter1.pdf", **row("good", "good", 0.8)}],
         "group_rank": 80},
        {"student": "cy_lee", "transcript": "cy_lee.pdf", "letters": [], "group_rank": None},
        {"student": "dan_wu", "transcript": None,
         "letters": [{"file": "dan_wu_letter1.pdf", **row(None, None, None)}], "group_rank": None},
    ]


@pytest.mark.parametrize("ranks, expected", [
    (["excellent", "excellent", "excellent"], 100),
    (["excellent"], 100),
    (["excellent", "good", "good"], 90),
    (["excellent", "excellent", "good"], 90),
    (["good", "good", "good"], 80),
    (["good", "normal", "normal"], 70),
    (["excellent", "good", "normal"], 70),
    (["excellent", "normal"], 70),
    (["normal", "normal", "normal"], 60),
    (["good", None], 80),
    ([None, None], None),
    ([], None),
])
def test_group_rank_rules(ranks, expected):
    from letter_builder.students import group_rank

    assert group_rank(ranks) == expected


def test_letter_ranks_are_the_post_ranked_ones(tmp_path):
    from letter_builder.students import letter_ranks

    rank_letters.write_ranks(tmp_path / RANKS_CSV, [("a_letter1.txt", "excellent", 0.5),
                                                    ("a_letter2.txt", "good", 0.9)])
    assert {stem: row["rank"] for stem, row in letter_ranks(tmp_path).items()} == {
        "a_letter1": "good", "a_letter2": "good"}
    assert letter_ranks(tmp_path)["a_letter1"]["jev_rank"] == "excellent"



def test_student_key_strips_only_the_letter_suffix():
    from letter_builder.students import student_key

    assert student_key("yan_letter3.pdf") == "yan"
    assert student_key("letterman_letter10.txt") == "letterman"
    assert student_key("ann.pdf") == "ann"


@pytest.mark.parametrize("mode, shown", [("dev", True), ("prod", False), (None, False)])
def test_jev_rank_and_confidence_only_in_dev_run_mode(tmp_path, letters, mode, shown):
    folder, config, work = letters
    pdfs = tmp_path / "in" / "transcripts"
    pdfs.mkdir()
    (pdfs / "ann.pdf").write_bytes(b"%PDF ann")
    config.write_text(CONFIG.format(tmp=tmp_path) + (f"run_mode: {mode}\n" if mode else ""))
    rank_letters.write_ranks(work / RANKS_CSV, [("ann_letter1.txt", "excellent", 0.55)])
    client = create_app(config).test_client()
    letter = client.get("/transcripts/view/student", query_string={"file": "ann.pdf"}).get_json()["letters"][0]
    assert letter["rank"] == "good"
    if shown:
        assert letter["jev_rank"] == "excellent" and letter["confidence"] == 0.55
    else:
        assert "jev_rank" not in letter and "confidence" not in letter


def test_every_view_page_has_whole_scripts(client):
    """Each <script> closes before the next opens: an unclosed one turns the rest of
    the page's JavaScript into a syntax error, and no student dialog opens."""
    import re
    for url in ("/transcripts/view", "/letters/view", "/profiles/view", "/view/summary"):
        html = client.get(url).get_data(as_text=True)
        assert html.count("<script>") == html.count("</script>"), url
        for script in re.findall(r"<script>(.*?)</script>", html, re.S):
            assert "<script" not in script, url
        assert "window.openStudent" in html and "window.studentRows" in html, url
