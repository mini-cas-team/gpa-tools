import io
import threading
import time

import pytest

import browser_view.app as app_module
from browser_view.app import create_app
from category_builder import definer
from category_builder.common import RunError

from conftest import FakeLLM, common_json

GPA_HEADER = ("student,institution,program,STEM_GPA,all_courses_GPA,"
              "stem_courses,total_courses,stem_all_pct,file\n")


@pytest.fixture
def folders(tmp_path):
    """work-folder/llm with STEM results, pdf-folder with ann.pdf, and config.yaml."""
    built, pdfs = tmp_path / "work" / "llm", tmp_path / "pdfs"
    built.mkdir(parents=True)
    pdfs.mkdir()
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
    config.write_text(f"# my settings\npdf-folder: {pdfs}\nout-folder: {tmp_path / 'out'}\n"
                      f"work-folder: {tmp_path / 'work'}\nmin-category-courses: 3\n"
                      "category-definitions:\n  STEM: Science and maths.\n"
                      "log-folder: /nowhere  # keep me\n")
    return built, config


@pytest.fixture
def client(folders):
    return create_app(folders[1]).test_client()


def courses_for(client, name, category="STEM", builder="llm"):
    return client.get("/view/courses",
                      query_string={"builder": builder, "category": category, "file": name})


# --- pages ----------------------------------------------------------------------

def test_two_tabs_opening_on_upload(client):
    upload = client.get("/").get_data(as_text=True)
    view = client.get("/view").get_data(as_text=True)
    for body in (upload, view):
        assert 'href="/"' in body and 'href="/view"' in body
    assert '<a href="/" class="active"' in upload
    assert '<a href="/view" class="active"' in view


def test_upload_page_has_the_form_and_category_panel(client, tmp_path):
    body = client.get("/").get_data(as_text=True)
    assert 'id="upload-form"' in body and str(tmp_path / "work") in body
    assert 'class="split"' in body
    assert body.index("upload-form") < body.index('id="category-heading"')
    order = [body.index(x) for x in ('id="category-select"', 'id="category-update"',
                                     'id="category-add"')]
    assert order == sorted(order)


def test_favicon_is_empty(client):
    assert client.get("/favicon.ico").status_code == 204


def test_a_missing_config_still_serves_pages(tmp_path):
    client = create_app(tmp_path / "absent.yaml").test_client()
    assert client.get("/").status_code == 200
    assert client.get("/view").status_code == 200


# --- view -----------------------------------------------------------------------

def test_view_shows_every_student_with_a_min_courses_filter(client, folders):
    built, _ = folders
    body = client.get("/view").get_data(as_text=True)  # the active builder is the default
    assert f"{built}/stem_gpa.csv" in body
    assert 'data-file="ann.pdf"' in body and "3.900" in body
    # No ranking server-side: Bob is listed; the filter (default from config) hides him.
    assert 'data-file="bob.pdf"' in body and 'data-courses="1"' in body
    assert 'id="min-courses"' in body and 'value="3"' in body
    assert '<option value="llm" selected>' in body
    assert "Held back" not in body


def test_view_picks_the_requested_category(client, folders):
    built, _ = folders
    (built / "law_gpa.csv").write_text(
        GPA_HEADER.replace("STEM", "LAW").replace("stem", "law")
        + "Cy Doe,TEST UNIVERSITY,Law,3.2,3.0,4,8,50.0,cy.pdf\n")
    body = client.get("/view", query_string={"category": "law"}).get_data(as_text=True)
    assert 'data-file="cy.pdf"' in body and 'data-file="ann.pdf"' not in body


def test_view_explains_missing_results_and_unknown_builders(client, folders):
    built, _ = folders
    for path in built.iterdir():
        path.unlink()
    assert "has no results" in client.get("/view").get_data(as_text=True)
    assert "unknown category builder" in client.get(
        "/view", query_string={"builder": "nope"}).get_data(as_text=True)


def test_view_never_reads_out_folder(client, folders, tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "law_gpa.csv").write_text(GPA_HEADER.replace("STEM", "LAW"))
    assert "LAW" not in client.get("/view").get_data(as_text=True)


def test_courses_come_from_the_courses_csv(client, folders):
    data = courses_for(client, "ann.pdf").get_json()
    assert data["student"] == "Ann Lee" and data["gpa_capped"] == 3.9
    assert [(c["code"], c["in_category"], c["credits"]) for c in data["courses"]] == [
        ("PHYS 101", True, 4.0), ("ENGL 101", False, 3.0)]
    assert client.get(data["pdf_url"]).data == b"%PDF-1.4 ann"


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


def test_pdf_is_found_in_work_folder_first(client, folders, tmp_path):
    (tmp_path / "work" / "ann.pdf").write_bytes(b"%PDF uploaded")
    r = client.get("/view/pdf", query_string={"file": "ann.pdf"})
    assert r.data == b"%PDF uploaded" and r.mimetype == "application/pdf"


def test_view_rejects_paths_unknown_students_categories_and_builders(client, folders):
    for name in ("../config.yaml", "nobody.pdf", ""):
        assert courses_for(client, name).status_code == 404
    assert courses_for(client, "ann.pdf", category="LAW").status_code == 404
    assert courses_for(client, "ann.pdf", builder="nope").status_code == 404
    assert client.get("/view/pdf", query_string={"file": "../config.yaml"}).status_code == 404


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


def test_upload_converts_uploaded_pdfs_into_work_folder(client, fake_convert, tmp_path):
    data = upload(client, [("a.pdf", b"%PDF a"), ("odd.pdf", b"%PDF odd"),
                           ("s.pdf", b"%PDF scanned")]).get_json()
    assert data["ok"] and data["source"] == "3 uploaded file(s)" and data["pdf_count"] == 3
    assert data["converted"] == [{"file": "a.pdf", "warnings": []},
                                 {"file": "odd.pdf", "warnings": ["odd label"]}]
    assert data["failures"] == [{"file": "s.pdf", "error": "no text layer"}]
    work = tmp_path / "work"
    assert (work / "a.common.json").is_file()
    assert (work / "a.pdf").read_bytes() == b"%PDF a"  # kept for View


def test_upload_strips_client_paths(client, fake_convert, tmp_path):
    assert upload(client, [("../../evil.pdf", b"%PDF")]).get_json()["ok"]
    assert fake_convert == ["evil.pdf"] and (tmp_path / "work" / "evil.pdf").is_file()


def test_upload_without_files_converts_pdf_folder(client, fake_convert):
    data = client.post("/upload", data={}, content_type="multipart/form-data").get_json()
    assert data["ok"] and fake_convert == ["ann.pdf"] and data["source"].endswith("pdfs")


def test_upload_rejects_non_pdfs(client, fake_convert):
    r = upload(client, [("notes.txt", b"x")])
    assert r.status_code == 400 and "not a PDF" in r.get_json()["error"]
    assert fake_convert == []


def test_upload_reports_when_nothing_converted(client, fake_convert):
    data = upload(client, [("s.pdf", b"scanned")]).get_json()
    assert not data["ok"] and data["error"] == "no PDF could be converted"


def test_upload_needs_a_work_folder(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("pdf-folder: /x\n")
    r = create_app(config).test_client().post("/upload", data={})
    assert r.status_code == 400 and "work-folder" in r.get_json()["error"]


# --- categories -----------------------------------------------------------------

@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setitem(definer.PROVIDERS, "ollama", lambda c: llm)
    return llm


def test_categories_list_config_definitions_in_file_order(client, folders):
    data = client.get("/categories").get_json()
    assert data == {"categories": {"STEM": "Science and maths."}, "names": ["STEM"],
                    "path": str(folders[1]), "builders": ["llm", "jev"], "active_builder": "llm",
                    "definer": ["chatgpt (window)", "gemini (window)", "ollama"]}
    config = folders[1]
    config.write_text(config.read_text().replace(
        "  STEM: Science and maths.\n", "  ZOO: Animals.\n  STEM: Science and maths.\n"))
    assert client.get("/categories").get_json()["names"] == ["ZOO", "STEM"]


def test_draft_falls_back_to_the_llm_without_saving(client, fake_llm, folders):
    data = client.post("/categories/draft", data={"name": " business "}).get_json()
    assert (data["name"], data["definition"], data["source"]) == (
        "BUSINESS", "BUSINESS means science.", "ollama")
    # chatgpt and gemini (no browser in tests) each fail twice: one try plus one retry
    assert [(a["step"], a["attempt"], a["error"] is None) for a in data["attempts"]] == [
        ("chatgpt", 1, False), ("chatgpt", 2, False), ("gemini", 1, False),
        ("gemini", 2, False), ("ollama", 1, True)]
    assert "no browser in tests" in data["attempts"][0]["error"]
    assert "BUSINESS" not in folders[1].read_text()


def test_draft_reports_every_failed_try(client, monkeypatch):
    class Down(FakeLLM):
        def define(self, category):
            raise OSError("refused")

    monkeypatch.setitem(definer.PROVIDERS, "ollama", lambda c: Down())
    r = client.post("/categories/draft", data={"name": "LAW"})
    data = r.get_json()
    assert r.status_code == 502 and "every step failed" in data["error"]
    assert [a["step"] for a in data["attempts"]] == ["chatgpt", "chatgpt", "gemini", "gemini", "ollama"]
    assert "refused" in data["attempts"][-1]["error"]


def test_draft_rejects_an_unknown_definer_mode(client, folders):
    config = folders[1]
    config.write_text(config.read_text() + "category-definer:\n  mode: nope\n")
    r = client.post("/categories/draft", data={"name": "LAW"})
    assert r.status_code == 400 and "nope" in r.get_json()["error"]
    assert client.get("/categories").get_json()["definer"] == []


def test_save_writes_only_the_definitions_section(client, folders):
    config = folders[1]
    r = client.post("/categories", data={"name": "business",
                                         "definition": "  Edited: management, finance.  "})
    assert r.get_json()["categories"] == {"STEM": "Science and maths.",
                                          "BUSINESS": "Edited: management, finance."}
    text = config.read_text()
    assert text.startswith("# my settings\npdf-folder:")
    assert "log-folder: /nowhere  # keep me" in text
    assert "BUSINESS: 'Edited: management, finance.'" in text


def test_replace_needs_an_existing_category(client, folders):
    r = client.post("/categories", data={"name": "stem", "definition": "Edited.", "replace": "1"})
    assert r.get_json()["categories"] == {"STEM": "Edited."}
    r = client.post("/categories", data={"name": "LAW", "definition": "Law.", "replace": "1"})
    assert r.status_code == 404 and "LAW" not in folders[1].read_text()


@pytest.mark.parametrize("path, form, status", [
    ("/categories/draft", {"name": "../x"}, 400),
    ("/categories/draft", {"name": "1ABC"}, 400),
    ("/categories/draft", {"name": "A" * 41}, 400),
    ("/categories/draft", {"name": "stem"}, 409),
    ("/categories", {"name": "STEM", "definition": "again"}, 409),
    ("/categories", {"name": "LAW", "definition": " "}, 400),
])
def test_bad_category_requests_never_reach_the_llm(client, fake_llm, path, form, status):
    assert client.post(path, data=form).status_code == status
    assert fake_llm.defined == []


# --- runs -----------------------------------------------------------------------

def test_estimate_counts_distinct_unanswered_courses(client, tmp_path):
    common_json(tmp_path / "work", "ann", [("MATH 1", "Calc", "A", 3, 4.0), ("ENGL 1", "Essay", "A", 3, 4.0)])
    common_json(tmp_path / "work", "bob", [("MATH 1", "Calc", "A", 3, 4.0), ("PHYS 1", "Mech", "A", 3, 4.0)])
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
def llm_builder():
    import llm_category.builder

    return llm_category.builder


def test_run_goes_in_the_background_then_publishes(client, monkeypatch, folders, tmp_path,
                                                   llm_builder):
    built, _ = folders
    seen = []

    def fake_run(cfg, category, progress=None):
        seen.append((cfg["folder"], category))
        progress(1, 2)
        progress(2, 2)
        return built / "stem_gpa.csv"

    monkeypatch.setattr(llm_builder, "run", fake_run)
    assert client.get("/runs/status").get_json() == {"status": "idle"}
    assert client.post("/runs", data={"category": "LAW"}).status_code == 404
    assert client.post("/runs", data={"category": "stem", "builder": "nope"}).status_code == 400
    assert client.post("/runs", data={"category": "stem", "builder": "llm"}).get_json()["status"] \
        in ("running", "done")
    state = wait_for_job(client)
    assert seen == [(built, "STEM")]
    assert state["status"] == "done" and state["done"] == 2 and state["total"] == 2
    assert state["builder"] == "llm" and state["folder"] == str(built) and "elapsed" in state
    published = tmp_path / "out" / "stem_gpa.csv"
    assert state["published"] == str(published)
    assert published.read_text() == (built / "stem_gpa.csv").read_text()


def test_a_failed_run_reports_its_error_and_publishes_nothing(client, monkeypatch, tmp_path,
                                                              llm_builder):
    def fake_run(cfg, category, progress=None):
        raise RunError("Cannot reach Ollama")

    monkeypatch.setattr(llm_builder, "run", fake_run)
    client.post("/runs", data={"category": "STEM"})
    state = wait_for_job(client)
    assert state == {**state, "status": "error", "error": "Cannot reach Ollama"}
    assert not (tmp_path / "out").exists()


def test_only_one_run_at_a_time(client, monkeypatch, folders, llm_builder):
    release = threading.Event()

    def slow_run(cfg, category, progress=None):
        release.wait(5)
        return folders[0] / "stem_gpa.csv"

    monkeypatch.setattr(llm_builder, "run", slow_run)
    assert client.post("/runs", data={"category": "STEM"}).status_code == 200
    r = client.post("/runs", data={"category": "STEM"})
    assert r.status_code == 409 and r.get_json()["status"] == "running"
    release.set()
    assert wait_for_job(client)["status"] == "done"
