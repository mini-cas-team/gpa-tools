import io
import threading
import time

import json
from pathlib import Path

import pytest

import browser_view.app as app_module
from browser_view.app import create_app
from category_builder import definer
from category_builder.common import RunError

from conftest import FakeDefiner, common_json

GPA_HEADER = ("student,institution,program,STEM_GPA,all_courses_GPA,"
              "stem_courses,total_courses,stem_all_pct,file\n")


@pytest.fixture
def folders(tmp_path):
    """work-folder/jev with STEM results, <input-folder>/transcripts with ann.pdf, and config.yaml."""
    built, pdfs = tmp_path / "work" / "jev", tmp_path / "in" / "transcripts"
    built.mkdir(parents=True)
    pdfs.mkdir(parents=True)
    (pdfs / "ann.pdf").write_bytes(b"%PDF-1.4 ann")
    (built / "stem_gpa.csv").write_text(
        GPA_HEADER
        + "Ann Lee,TEST UNIVERSITY,Physics,3.9,3.5,5,10,50.0,ann.pdf\n"
        + "Bob Ray,TEST UNIVERSITY,History,,3.1,1,10,10.0,bob.pdf\n")
    (built / "stem_courses.csv").write_text(
        "student,file,term,code,title,grade,credits,gpa_4,is_stem\n"
        "Ann Lee,ann.pdf,Fall 2024,PHYS 101,Mechanics,A,4.0,4.0,yes\n"
        "Ann Lee,ann.pdf,Fall 2024,ENGL 101,Writing,B,3.0,3.0,no\n"
        "Bob Ray,bob.pdf,Fall 2024,HIST 101,Rome,A,3.0,4.0,no\n")
    config = tmp_path / "config.yaml"
    config.write_text(f"# my settings\ninput-folder: {tmp_path / 'in'}\nout-folder: {tmp_path / 'out'}\n"
                      f"work-folder: {tmp_path / 'work'}\nmin-category-courses: 3\n"
                      "category-definitions:\n  STEM: Science and maths.\n"
                      "log-folder: /nowhere  # keep me\nrun_mode: dev\n")
    return built, config


@pytest.fixture
def client(folders):
    return create_app(folders[1]).test_client()


def courses_for(client, name, category="STEM", builder="jev"):
    return client.get("/transcripts/view/student",
                      query_string={"builder": builder, "category": category, "file": name})


# --- pages ----------------------------------------------------------------------

def test_root_is_a_welcome_page_linking_every_page(client):
    body = client.get("/").get_data(as_text=True)
    assert "Welcome." in body and 'id="upload-form"' not in body
    for href in ("/transcripts/ftp-upload", "/transcripts/view",
                 "/letters/ftp-upload", "/letters/view", "/profiles/upload", "/profiles/view",
                 "/summary", "/view/rank"):
        assert f'<a href="{href}">' in body
    assert "/transcripts/category-build" not in body
    assert 'aria-current="page"' not in body  # no nav item is the welcome page


def test_left_bar_groups_pages_by_action(client):
    body = client.get("/transcripts/view").get_data(as_text=True)
    nav = body[body.index('<nav class="side"'):body.index("</nav>")]
    order = [nav.index(x) for x in ('<span class="group">Upload</span>',
                                    'href="/summary" class="sub">Summary',
                                    'href="/transcripts/ftp-upload" class="sub">Transcripts',
                                    'href="/letters/ftp-upload" class="sub">Recommendations',
                                    'href="/profiles/upload" class="sub">Profiles',
                                    '<span class="group">View</span>',
                                    'href="/view/rank" class="sub">Rank',
                                    'href="/transcripts/view" class="sub active" aria-current="page">Transcripts',
                                    'href="/letters/view" class="sub">Recommendations',
                                    'href="/profiles/view" class="sub">Profiles')]
    assert order == sorted(order)
    assert "category-build" not in nav


def test_transcript_pages_share_the_transcripts_path(client):
    upload = client.get("/transcripts/ftp-upload").get_data(as_text=True)
    view = client.get("/transcripts/view").get_data(as_text=True)
    for body in (upload, view):
        assert 'href="/transcripts/view"' in body and 'href="/view"' not in body
        # no "Transcripts and Recommendation Letters" title bar on the pages
        assert "Transcripts and Recommendation Letters</a>" not in body and "<h1>" not in body
    assert 'href="/transcripts/ftp-upload" class="sub active" aria-current="page">Transcripts' in upload
    assert 'action="/transcripts/view"' in view
    assert client.get("/view").status_code == 404


def test_upload_page_has_the_form_and_category_panel(client, tmp_path):
    body = client.get("/transcripts/ftp-upload").get_data(as_text=True)
    assert 'id="upload-form"' in body and str(tmp_path / "work" / "transcripts") in body
    assert str(tmp_path / "in" / "transcripts") in body
    assert 'class="split"' in body and " hidden>" not in body.split('<div class="split')[1][:400]
    assert body.index('>Transcripts Upload</h2>') < body.index('>Category Build</h2>')
    assert body.index("upload-form") < body.index('id="category-heading"')
    order = [body.index(x) for x in ('id="category-select"', 'id="category-update"',
                                     'id="category-add"')]
    assert order == sorted(order)


def test_old_transcript_addresses_lead_to_the_upload_page(client):
    for old in ("/transcripts", "/transcripts/category-build"):
        r = client.get(old)
        assert r.status_code == 302 and r.headers["Location"].endswith("/transcripts/ftp-upload")
    assert client.get("/transcripts/other").status_code == 404


def test_favicon_is_empty(client):
    assert client.get("/favicon.ico").status_code == 204


def test_a_missing_config_still_serves_pages(tmp_path):
    client = create_app(tmp_path / "absent.yaml").test_client()
    assert client.get("/").status_code == 200
    assert client.get("/transcripts/ftp-upload").status_code == 200
    assert client.get("/transcripts/view").status_code == 200


# --- view -----------------------------------------------------------------------

def test_view_shows_every_student_with_a_min_courses_filter(client, folders):
    built, _ = folders
    body = client.get("/transcripts/view").get_data(as_text=True)  # the active builder is the default
    assert f"{built}/stem_gpa.csv" in body
    assert 'data-file="ann.pdf"' in body and "3.900" in body
    # No ranking server-side: Bob is listed; the filter (default from config) hides him.
    assert 'data-file="bob.pdf"' in body and 'data-courses="1"' in body
    assert 'id="min-courses"' in body and 'value="3"' in body
    assert '<option value="jev" selected>' in body
    assert "Held back" not in body


def test_view_rows_highlight_and_the_student_link_opens_the_dialog(client, folders):
    body = client.get("/transcripts/view").get_data(as_text=True)
    rows = body.split("<tbody>")[1].split("</tbody>")[0]
    # As on Recommendations → View: the name is the link; the row itself is not a button
    assert '<td><a href="#" class="student-link" data-file="ann.pdf">' in rows
    assert 'class="student"' not in rows and "tabindex" not in rows
    assert 'id="row-tip"' not in body
    assert 'row.classList.add("selected")' in body and 'closest("a.student-link")' in body
    letters = client.get("/letters/view").get_data(as_text=True)
    assert 'row.classList.add("selected")' in letters


def test_view_has_no_recommend_letter_column(client, tmp_path):
    from letter_builder import rank_letters

    letters, ranked = tmp_path / "in" / "letters", tmp_path / "work" / "letters"
    letters.mkdir(parents=True)
    ranked.mkdir(parents=True)
    (letters / "ann_letter1.pdf").write_bytes(b"%PDF")
    rank_letters.write_ranks(ranked / "letter_ranks.csv", [("ann_letter1.txt", "excellent", 0.9)])
    body = client.get("/transcripts/view").get_data(as_text=True)
    table = body[body.index('<table id="ranking">'):body.index("</table>")]
    assert "Recommend letter" not in table  # (the student dialog still gives it)
    ann = body.split('data-file="ann.pdf"')[1].split("</tr>")[0]
    assert ann.count("<td") == 7  # Student, Institution, Program, GPA, All, courses, Total, %

def test_view_picks_the_requested_category(client, folders):
    built, _ = folders
    (built / "law_gpa.csv").write_text(
        GPA_HEADER.replace("STEM", "LAW").replace("stem", "law")
        + "Cy Doe,TEST UNIVERSITY,Law,3.2,3.0,4,8,50.0,cy.pdf\n")
    body = client.get("/transcripts/view", query_string={"category": "law"}).get_data(as_text=True)
    assert 'data-file="cy.pdf"' in body and 'data-file="ann.pdf"' not in body


def test_view_explains_missing_results_and_unknown_builders(client, folders):
    built, _ = folders
    for path in built.iterdir():
        path.unlink()
    assert "has no results" in client.get("/transcripts/view").get_data(as_text=True)
    assert "unknown category builder" in client.get(
        "/transcripts/view", query_string={"builder": "nope"}).get_data(as_text=True)


def test_view_never_reads_out_folder(client, folders, tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "law_gpa.csv").write_text(GPA_HEADER.replace("STEM", "LAW"))
    assert "LAW" not in client.get("/transcripts/view").get_data(as_text=True)


def test_courses_come_from_the_courses_csv(client, folders):
    data = courses_for(client, "ann.pdf").get_json()
    assert data["student"] == "Ann Lee" and data["gpa_capped"] == 3.9
    assert [(c["code"], c["in_category"], c["credits"]) for c in data["courses"]] == [
        ("PHYS 101", True, 4.0), ("ENGL 101", False, 3.0)]
    assert client.get(data["pdf_url"]).data == b"%PDF-1.4 ann"


def test_student_detail_lists_the_students_letters_with_post_ranks(client, tmp_path, folders):
    from letter_builder import rank_letters

    config = folders[1]  # prod: Jev's own rank and confidence stay hidden
    config.write_text(config.read_text().replace("run_mode: dev", "run_mode: prod"))

    letters, ranked = tmp_path / "in" / "letters", tmp_path / "work" / "letters"
    letters.mkdir(parents=True)
    ranked.mkdir(parents=True)
    for name in ("ann_letter1.pdf", "ann_letter2.pdf", "bob_letter1.pdf"):
        (letters / name).write_bytes(b"%PDF")
    rank_letters.write_ranks(ranked / "letter_ranks.csv", [("ann_letter1.txt", "excellent", 0.5),
                                                            ("ann_letter2.txt", "good", 0.9)])
    data = courses_for(client, "ann.pdf").get_json()
    assert data["letter_rank"] == 80  # excellent at 0.5 is post-ranked good: all good
    assert data["letters"] == [
        {"file": "ann_letter1.pdf", "rank": "good", "pdf_url": "/letters/pdf?file=ann_letter1.pdf"},
        {"file": "ann_letter2.pdf", "rank": "good", "pdf_url": "/letters/pdf?file=ann_letter2.pdf"}]
    assert client.get(data["letters"][0]["pdf_url"]).status_code == 200
    bob = courses_for(client, "bob.pdf").get_json()
    assert bob["letters"] == [{"file": "bob_letter1.pdf", "rank": None,
                               "pdf_url": "/letters/pdf?file=bob_letter1.pdf"}]
    assert bob["letter_rank"] is None


def test_a_student_without_a_pdf_still_has_courses(client, folders):
    data = courses_for(client, "bob.pdf").get_json()
    assert data["gpa_capped"] is None and data["category_courses"] == 1
    assert [c["code"] for c in data["courses"]] == ["HIST 101"]
    assert data["pdf_url"] is None


def test_courses_need_the_courses_csv(client, folders):
    built, _ = folders
    (built / "stem_courses.csv").unlink()
    r = courses_for(client, "ann.pdf")
    assert r.status_code == 404 and "no course list" in r.get_json()["error"]


def test_pdf_is_served_from_the_input_folder_only(client, folders, tmp_path):
    r = client.get("/transcripts/view/pdf", query_string={"file": "ann.pdf"})
    assert r.data == b"%PDF-1.4 ann" and r.mimetype == "application/pdf"
    (tmp_path / "work" / "transcripts").mkdir(parents=True)
    (tmp_path / "work" / "transcripts" / "cy.pdf").write_bytes(b"%PDF")  # not input
    assert client.get("/transcripts/view/pdf", query_string={"file": "cy.pdf"}).status_code == 404


def test_view_rejects_paths_unknown_students_categories_and_builders(client, folders):
    for name in ("../config.yaml", "nobody.pdf", ""):
        assert courses_for(client, name).status_code == 404
    assert courses_for(client, "ann.pdf", category="LAW").status_code == 404
    assert courses_for(client, "ann.pdf", builder="nope").status_code == 404
    assert client.get("/transcripts/view/pdf", query_string={"file": "../config.yaml"}).status_code == 404


# --- upload ---------------------------------------------------------------------

@pytest.fixture
def fake_convert(monkeypatch):
    """Stand-in for pdftotext + txt2common: records calls, writes the two files."""
    calls = []

    def convert(pdf, work):
        calls.append(pdf.name)
        if b"scanned" in pdf.read_bytes():
            raise ValueError("no text layer")
        (work / f"{pdf.stem}.txt").write_text("text")
        (work / f"{pdf.stem}.common.json").write_text("{}")
        return {"warnings": ["odd label"] if pdf.stem == "odd" else []}

    monkeypatch.setattr(app_module, "pdf_to_common_json", convert)
    return calls


def upload(client, files):
    return client.post("/upload", data={"pdfs": [(io.BytesIO(b), n) for n, b in files]},
                       content_type="multipart/form-data")


def test_upload_saves_uploads_as_input_and_converts_them_into_work_folder(client, fake_convert, tmp_path):
    data = upload(client, [("a.pdf", b"%PDF a"), ("odd.pdf", b"%PDF odd"),
                           ("s.pdf", b"%PDF scanned")]).get_json()
    assert data["ok"] and data["source"] == "3 uploaded file(s)" and data["pdf_count"] == 3
    assert data["converted"] == [{"file": "a.pdf", "warnings": []},
                                 {"file": "odd.pdf", "warnings": ["odd label"]}]
    assert data["failures"] == [{"file": "s.pdf", "error": "no text layer"}]
    work = tmp_path / "work" / "transcripts"
    assert (work / "a.common.json").is_file() and not (work / "a.pdf").exists()
    assert (tmp_path / "in" / "transcripts" / "a.pdf").read_bytes() == b"%PDF a"  # kept for View


def test_upload_strips_client_paths(client, fake_convert, tmp_path):
    assert upload(client, [("../../evil.pdf", b"%PDF")]).get_json()["ok"]
    assert fake_convert == ["evil.pdf"] and (tmp_path / "in" / "transcripts" / "evil.pdf").is_file()


def test_upload_without_files_converts_the_input_folder(client, fake_convert, tmp_path):
    data = client.post("/upload", data={}, content_type="multipart/form-data").get_json()
    assert data["ok"] and fake_convert == ["ann.pdf"] and data["source"] == str(tmp_path / "in" / "transcripts")
    assert data["work_folder"] == str(tmp_path / "work" / "transcripts")


def test_upload_rejects_non_pdfs(client, fake_convert):
    r = upload(client, [("notes.txt", b"x")])
    assert r.status_code == 400 and "not a PDF" in r.get_json()["error"]
    assert fake_convert == []


def test_upload_reports_when_nothing_converted(client, fake_convert):
    data = upload(client, [("s.pdf", b"scanned")]).get_json()
    assert not data["ok"] and data["error"] == "no PDF could be converted"


def test_upload_needs_a_work_folder(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("input-folder: /x\n")
    r = create_app(config).test_client().post("/upload", data={})
    assert r.status_code == 400 and "work-folder" in r.get_json()["error"]


# --- categories -----------------------------------------------------------------

@pytest.fixture
def fake_gemini(monkeypatch):
    """Gemini drafts; ChatGPT still fails (no browser in tests)."""
    gemini = FakeDefiner()
    monkeypatch.setitem(definer.PROVIDERS, "gemini", lambda c: gemini)
    return gemini


def test_categories_list_the_definitions_repo(client, folders):
    """config.yaml's old category-definitions section is moved into the repo folder."""
    data = client.get("/categories").get_json()
    repo = Path(data["path"])
    assert data == {"categories": {"STEM": "Science and maths."}, "names": ["STEM"],
                    "path": str(repo), "origins": {"STEM": "local"},
                    "builders": ["jev"], "active_builder": "jev",
                    "definer": ["chatgpt (window)", "gemini (window)"],
                    "mode": "dev"}
    assert (repo / "STEM.json").exists()
    text = folders[1].read_text()
    assert "category-definitions" not in text and f"definitions-folder: {repo}" in text
    (repo / "ZOO.json").write_text('{"category": "ZOO", "definition": "Animals.", "origin": "local"}')
    assert client.get("/categories").get_json()["names"] == ["STEM", "ZOO"]


def test_draft_falls_back_to_the_next_site_without_saving(client, fake_gemini, folders):
    data = client.post("/categories/draft", data={"name": " business "}).get_json()
    assert (data["name"], data["definition"], data["source"]) == (
        "BUSINESS", "BUSINESS means science.", "gemini")
    # chatgpt (no browser in tests) fails twice: one try plus one retry
    assert [(a["step"], a["attempt"], a["error"] is None) for a in data["attempts"]] == [
        ("chatgpt", 1, False), ("chatgpt", 2, False), ("gemini", 1, True)]
    assert "no browser in tests" in data["attempts"][0]["error"]
    assert "BUSINESS" not in folders[1].read_text()


def test_draft_reports_every_failed_try(client):
    r = client.post("/categories/draft", data={"name": "LAW"})
    data = r.get_json()
    assert r.status_code == 502 and "every step failed" in data["error"]
    assert [a["step"] for a in data["attempts"]] == ["chatgpt", "chatgpt", "gemini", "gemini"]
    assert "no browser in tests" in data["attempts"][-1]["error"]


def test_draft_rejects_an_unknown_definer_mode(client, folders):
    config = folders[1]
    config.write_text(config.read_text() + "category-definer:\n  mode: nope\n")
    r = client.post("/categories/draft", data={"name": "LAW"})
    assert r.status_code == 400 and "nope" in r.get_json()["error"]
    assert client.get("/categories").get_json()["definer"] == []


def test_save_writes_the_repo_and_leaves_config_alone(client, folders):
    config = folders[1]
    before = client.get("/categories").get_json()  # the first read moves the old section
    text = config.read_text()
    r = client.post("/categories", data={"name": "business",
                                         "definition": "  Edited: management, finance.  "})
    body = r.get_json()
    assert body["categories"] == {"STEM": "Science and maths.",
                                  "BUSINESS": "Edited: management, finance."}
    # No cloud in this config: kept as this PC's own, offered to the cloud later.
    assert (body["origin"], body["shared"]) == ("local", "pending")
    assert config.read_text() == text
    assert text.startswith("# my settings\ninput-folder:")
    assert "log-folder: /nowhere  # keep me" in text
    saved = json.loads((Path(before["path"]) / "BUSINESS.json").read_text())
    assert saved["definition"] == "Edited: management, finance." and saved["pending"] is True


def test_replace_needs_an_existing_category(client, folders):
    r = client.post("/categories", data={"name": "stem", "definition": "Edited.", "replace": "1"})
    assert r.get_json()["categories"] == {"STEM": "Edited."}
    r = client.post("/categories", data={"name": "LAW", "definition": "Law.", "replace": "1"})
    repo = Path(client.get("/categories").get_json()["path"])
    assert r.status_code == 404 and not (repo / "LAW.json").exists()


@pytest.mark.parametrize("path, form, status", [
    ("/categories/draft", {"name": "../x"}, 400),
    ("/categories/draft", {"name": "1ABC"}, 400),
    ("/categories/draft", {"name": "A" * 41}, 400),
    ("/categories/draft", {"name": "stem"}, 409),
    ("/categories", {"name": "STEM", "definition": "again"}, 409),
    ("/categories", {"name": "LAW", "definition": " "}, 400),
])
def test_bad_category_requests_never_reach_the_definer(client, fake_gemini, path, form, status):
    assert client.post(path, data=form).status_code == status
    assert fake_gemini.defined == []


@pytest.fixture
def prod(folders, monkeypatch):
    """run_mode prod, with a cloud that shares STEM and LAW."""
    from category_builder import definitions as defs_module
    from test_category_builder import FakeCloud

    config = folders[1]
    config.write_text(config.read_text().replace("run_mode: dev", "run_mode: prod"))
    cloud = FakeCloud({"STEM": "Science and maths.", "LAW": "Law and legal studies."})
    monkeypatch.setattr(defs_module, "cloud_for", lambda raw: cloud)
    return cloud


def test_prod_takes_every_cloud_category_by_itself_and_never_drafts(client, prod, fake_gemini, folders):
    data = client.get("/categories").get_json()  # the first visit downloads the cloud's ones
    assert (data["mode"], data["definer"]) == ("prod", [])
    assert set(data["names"]) == {"STEM", "LAW"}
    prod.shared["MATH"] = "Mathematics."  # shared by the owner later: the next visit has it
    data = client.get("/categories").get_json()
    assert data["categories"]["MATH"] == "Mathematics." and data["origins"]["MATH"] == "cloud"
    prod.shared["ART"] = "Art."  # even a direct save takes the cloud's text, not the posted one
    r = client.post("/categories", data={"name": "ART", "definition": "My own words."}).get_json()
    assert (r["definition"], r["origin"], r["shared"]) == ("Art.", "cloud", "taken")
    del prod.shared["ART"]
    # a new one: no draft, no typing
    for path in ("/categories/draft", "/categories"):
        r = client.post(path, data={"name": "DANCE", "definition": "Dance."})
        assert r.status_code == 404 and "Ask the package owner" in r.get_json()["error"]
    assert fake_gemini.defined == [] and prod.created == []
    # no edits; going back to the cloud's text still works
    r = client.post("/categories", data={"name": "STEM", "definition": "Edited.", "replace": "1"})
    assert r.status_code == 403
    assert client.get("/categories").get_json()["categories"]["STEM"] == "Science and maths."


def test_prod_page_hides_the_developer_parts(client, prod):
    body = client.get("/transcripts/ftp-upload").get_data(as_text=True)
    assert '<label for="builder-select">' not in body and 'id="category-path"' not in body
    assert "const DEV = false" in body and 'id="category-build" hidden>Build</button>' in body
    assert ">Update</button>" not in body and ">Add</button>" not in body


def test_dev_page_shows_everything(client):
    body = client.get("/transcripts/ftp-upload").get_data(as_text=True)
    assert '<label for="builder-select">' in body and 'id="category-path"' in body
    assert "const DEV = true" in body and ">Update</button>" in body and ">Add</button>" in body
    assert 'id="category-build"' not in body


# --- runs -----------------------------------------------------------------------

def test_estimate_counts_distinct_unanswered_courses(client, tmp_path):
    common_json(tmp_path / "work" / "transcripts", "ann", [("MATH 1", "Calc", "A", 3, 4.0), ("ENGL 1", "Essay", "A", 3, 4.0)])
    common_json(tmp_path / "work" / "transcripts", "bob", [("MATH 1", "Calc", "A", 3, 4.0), ("PHYS 1", "Mech", "A", 3, 4.0)])
    data = client.get("/runs/estimate", query_string={"category": "stem"}).get_json()
    assert data["category"] == "STEM"
    assert (data["students"], data["courses"], data["pending"]) == (2, 3, 3)
    assert data["seconds"] == round(3 * data["seconds_per_answer"])


def test_estimate_errors(client):
    assert client.get("/runs/estimate", query_string={"category": "LAW"}).status_code == 404
    r = client.get("/runs/estimate", query_string={"category": "STEM"})  # no transcripts yet
    assert r.status_code == 400 and "common.json" in r.get_json()["error"]


def wait_for_job(client):
    for _ in range(100):
        state = client.get("/runs/status").get_json()
        if state["status"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("run did not finish")


@pytest.fixture
def jev_builder():
    import jev_category.builder

    return jev_category.builder


def test_run_goes_in_the_background_then_publishes(client, monkeypatch, folders, tmp_path,
                                                   jev_builder):
    built, _ = folders
    seen = []

    def fake_run(cfg, category, progress=None):
        seen.append((cfg["folder"], category))
        progress(1, 2)
        progress(2, 2)
        return built / "stem_gpa.csv"

    monkeypatch.setattr(jev_builder, "run", fake_run)
    assert client.get("/runs/status").get_json() == {"status": "idle"}
    assert client.post("/runs", data={"category": "LAW"}).status_code == 404
    assert client.post("/runs", data={"category": "stem", "builder": "nope"}).status_code == 400
    assert client.post("/runs", data={"category": "stem", "builder": "jev"}).get_json()["status"] \
        in ("running", "done")
    state = wait_for_job(client)
    assert seen == [(built, "STEM")]
    assert state["status"] == "done" and state["done"] == 2 and state["total"] == 2
    assert state["builder"] == "jev" and state["folder"] == str(built) and "elapsed" in state
    published = tmp_path / "out" / "stem_gpa.csv"
    assert state["published"] == str(published)
    assert published.read_text() == (built / "stem_gpa.csv").read_text()


def test_a_failed_run_reports_its_error_and_publishes_nothing(client, monkeypatch, tmp_path,
                                                              jev_builder):
    def fake_run(cfg, category, progress=None):
        raise RunError("Cannot reach the gpa-tools cloud")

    monkeypatch.setattr(jev_builder, "run", fake_run)
    client.post("/runs", data={"category": "STEM"})
    state = wait_for_job(client)
    assert state == {**state, "status": "error", "error": "Cannot reach the gpa-tools cloud"}
    assert not (tmp_path / "out").exists()


def test_only_one_run_at_a_time(client, monkeypatch, folders, jev_builder):
    release = threading.Event()

    def slow_run(cfg, category, progress=None):
        release.wait(5)
        return folders[0] / "stem_gpa.csv"

    monkeypatch.setattr(jev_builder, "run", slow_run)
    assert client.post("/runs", data={"category": "STEM"}).status_code == 200
    r = client.post("/runs", data={"category": "STEM"})
    assert r.status_code == 409 and r.get_json()["status"] == "running"
    release.set()
    assert wait_for_job(client)["status"] == "done"


# --- prod hides the components without data ------------------------------------

def _nav(body):
    return body[body.index('<nav class="side"'):body.index("</nav>")]


def test_prod_hides_the_view_pages_rank_columns_and_config_rows_of_empty_components(client, prod, folders):
    """Only transcripts have data (built STEM results): no letters, no profiles."""
    common_json(folders[1].parent / "work" / "transcripts", "ann", [("PHYS 101", "Mechanics", "A", 4, 4.0)])
    body = client.get("/view/rank").get_data(as_text=True)
    nav = _nav(body)
    assert 'href="/transcripts/view"' in nav
    assert 'href="/letters/view"' not in nav and 'href="/profiles/view"' not in nav
    assert 'href="/letters/ftp-upload"' in nav and 'href="/profiles/upload"' in nav  # uploads stay
    config = body[body.index('class="rank-config"'):body.index("</table>", body.index('class="rank-config"'))]
    rows = config.split("<tr")[2:]  # after the header row
    assert [("hidden" in r.split(">")[0]) for r in rows] == [False, True, True]
    head = body[body.index("<thead"):body.index("</thead>")]
    assert "Transcript" in head and "Recommend Letter" not in head and "Profile" not in head
    assert 'id="recommendation-dialog"' in body and 'id="profile-dialog"' in body  # the script finds them


def test_prod_shows_a_component_once_it_has_data(client, prod, folders):
    tmp = folders[1].parent
    (tmp / "in" / "letters").mkdir(parents=True)
    (tmp / "in" / "letters" / "ann_letter1.pdf").write_bytes(b"%PDF-1.4")
    (tmp / "work" / "personal").mkdir(parents=True)
    (tmp / "work" / "personal" / "ann.json").write_text('{"name": "Ann Lee"}')
    common_json(tmp / "work" / "transcripts", "ann", [("PHYS 101", "Mechanics", "A", 4, 4.0)])
    body = client.get("/view/rank").get_data(as_text=True)
    nav = _nav(body)
    assert all(h in nav for h in ('href="/transcripts/view"', 'href="/letters/view"', 'href="/profiles/view"'))
    head = body[body.index("<thead"):body.index("</thead>")]
    assert "Recommend Letter" in head and "Profile" in head


def test_prod_without_any_data_has_no_rank_columns_and_no_builder_message(client, prod, folders):
    for csv_file in folders[0].glob("*.csv"):
        csv_file.unlink()
    body = client.get("/view/rank").get_data(as_text=True)
    nav = _nav(body)
    assert 'href="/view/rank"' in nav
    assert not any(h in nav for h in ('href="/transcripts/view"', 'href="/letters/view"', 'href="/profiles/view"'))
    assert "so there is no Transcript column" not in body


def test_dev_shows_every_component_even_without_data(client, folders):
    nav = _nav(client.get("/view/rank").get_data(as_text=True))
    assert all(h in nav for h in ('href="/transcripts/view"', 'href="/letters/view"', 'href="/profiles/view"'))


def test_an_empty_components_page_keeps_its_own_link_when_opened(client, prod):
    nav = _nav(client.get("/letters/view").get_data(as_text=True))
    assert 'href="/letters/view" class="sub active"' in nav
