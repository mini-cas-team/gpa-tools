import io
import json
import time

import pytest

import browser_view.app as app_module
from browser_view.app import create_app

RANK_HEADER = ("rank,student,institution,program,STEM_GPA,all_courses_GPA,"
               "stem_courses,total_courses,stem_all_pct,gate,file\n")


@pytest.fixture
def folders(tmp_path):
    out = tmp_path / "out"
    pdfs = tmp_path / "pdfs"
    out.mkdir()
    pdfs.mkdir()
    (pdfs / "ann.pdf").write_bytes(b"%PDF-1.4 ann")
    (out / "rank_stem_gpa.csv").write_text(
        RANK_HEADER
        + "1,Ann Lee,TEST UNIVERSITY,Physics,3.9,3.5,5,10,50.0,pass,ann.pdf\n"
        + ",Bob Ray,TEST UNIVERSITY,History,,3.1,1,10,10.0,fail,bob.pdf\n"
    )
    (out / "stem_courses.csv").write_text(
        "student,file,term,code,title,grade,credits,gpa_4,is_stem\n"
        "Ann Lee,ann.pdf,Fall 2024,PHYS 101,Mechanics,A,4.0,4.0,yes\n"
        "Ann Lee,ann.pdf,Fall 2024,ENGL 101,Writing,B,3.0,3.0,no\n"
        "Bob Ray,bob.pdf,Fall 2024,HIST 101,Rome,A,3.0,4.0,no\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(f"# my settings\npdf-folder: {pdfs}\nout-folder: {out}\n"
                      f"work-folder: {tmp_path / 'work'}\n"
                      "category-definitions:\n  STEM: Science and maths.\n"
                      "log-folder: /nowhere  # keep me\n")
    return out, config


@pytest.fixture
def client(folders):
    return create_app(folders[1]).test_client()


def test_two_tabs_opening_on_upload(client):
    upload = client.get("/").get_data(as_text=True)
    view = client.get("/view").get_data(as_text=True)
    for body in (upload, view):
        assert 'href="/"' in body and 'href="/view"' in body
        assert 'href="/run"' not in body
    assert '<a href="/" class="active"' in upload
    assert '<a href="/view" class="active"' in view


def test_view_reads_the_ranking_csv(client, folders):
    out, _ = folders
    body = client.get("/view").get_data(as_text=True)  # out-folder is the default
    assert f"{out}/rank_stem_gpa.csv" in body
    assert 'data-file="ann.pdf"' in body and "3.900" in body
    # gate=fail rows are held back, not ranked.
    assert 'data-file="bob.pdf"' not in body
    assert "Held back" in body and "Bob Ray" in body


def test_courses_come_from_the_courses_csv(client, folders):
    out, _ = folders
    data = client.get(
        "/view/courses", query_string={"folder": str(out), "category": "STEM", "file": "ann.pdf"}
    ).get_json()
    assert data["student"] == "Ann Lee" and data["gpa_capped"] == 3.9
    assert [(c["code"], c["in_category"]) for c in data["courses"]] == [
        ("PHYS 101", True), ("ENGL 101", False)]
    assert data["courses"][0]["term"] == "Fall 2024"
    assert client.get(data["pdf_url"]).data == b"%PDF-1.4 ann"


def test_gpa_ana_folder_falls_back_to_extracted_json(client, folders):
    out, _ = folders
    (out / "stem_courses.csv").unlink()
    (out / "extracted").mkdir()
    (out / "extracted" / "ann.stem.courses.json").write_text(json.dumps(
        {"courses": [{"code": "PHYS 101", "title": "Mechanics", "stem": True}]}))
    data = client.get(
        "/view/courses", query_string={"folder": str(out), "category": "STEM", "file": "ann.pdf"}
    ).get_json()
    assert data["courses"] == [{"code": "PHYS 101", "title": "Mechanics", "in_category": True}]


def test_view_rejects_paths_and_unknown_students(client, folders):
    out, _ = folders
    for name in ("../config.yaml", "nobody.pdf"):
        r = client.get("/view/courses",
                       query_string={"folder": str(out), "category": "STEM", "file": name})
        assert r.status_code == 404
    assert client.get("/view/pdf", query_string={"folder": str(out),
                                                 "file": "../config.yaml"}).status_code == 404


def test_view_reports_a_folder_without_rankings(client, tmp_path):
    body = client.get("/view", query_string={"folder": str(tmp_path)}).get_data(as_text=True)
    assert "no rankings" in body


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


def test_upload_converts_uploaded_pdfs_into_work_folder(client, fake_convert, tmp_path):
    response = client.post("/upload", data={"pdfs": [
        (io.BytesIO(b"%PDF a"), "a.pdf"), (io.BytesIO(b"%PDF odd"), "odd.pdf"),
        (io.BytesIO(b"%PDF scanned"), "s.pdf")]}, content_type="multipart/form-data")
    data = response.get_json()
    assert data["ok"] and data["source"] == "3 uploaded file(s)"
    assert data["converted"] == [{"file": "a.pdf", "warnings": []},
                                 {"file": "odd.pdf", "warnings": ["odd label"]}]
    assert data["failures"] == [{"file": "s.pdf", "error": "no text layer"}]
    work = tmp_path / "work"
    assert (work / "a.txt").is_file() and (work / "a.common.json").is_file()
    assert (work / "a.pdf").read_bytes() == b"%PDF a"  # upload kept for View


def test_upload_without_files_converts_pdf_folder(client, fake_convert):
    data = client.post("/upload", data={}, content_type="multipart/form-data").get_json()
    assert data["ok"] and fake_convert == ["ann.pdf"]
    assert data["source"].endswith("pdfs")


def test_upload_rejects_non_pdfs(client, fake_convert):
    response = client.post("/upload", data={"pdfs": [(io.BytesIO(b"x"), "notes.txt")]},
                           content_type="multipart/form-data")
    assert response.status_code == 400 and "not a PDF" in response.get_json()["error"]
    assert fake_convert == []


def test_upload_page_names_the_folders(client, tmp_path):
    body = client.get("/").get_data(as_text=True)
    assert 'id="upload-form"' in body and str(tmp_path / "work") in body


def test_upload_page_is_split_with_category_controls_in_order(client):
    body = client.get("/").get_data(as_text=True)
    assert 'class="split"' in body
    assert 'list="category-suggestions" autocomplete="off"' in body
    assert body.index("upload-form") < body.index('id="category-heading"')
    label, pick, update, add = (body.index(x) for x in (
        'for="category-select">Category', 'id="category-select"', 'id="category-update"',
        'id="category-add"'))
    assert label < pick < update < add
    assert 'id="category-reload"' not in body and "<dialog" not in body
    assert body.index('for="category-name">Name') < body.index('id="category-add-go"') \
        < body.index('id="category-add-cancel"')
    assert body.index('id="category-update-go"') < body.index('id="category-update-cancel"')


def test_categories_lists_config_definitions(client, folders):
    data = client.get("/categories").get_json()
    assert data["categories"] == {"STEM": "Science and maths."}
    assert data["names"] == ["STEM"]
    assert data["path"] == str(folders[1])


def test_names_keep_the_config_order(client, folders):
    config = folders[1]
    config.write_text(config.read_text().replace(
        "  STEM: Science and maths.\n", "  ZOO: Animals.\n  STEM: Science and maths.\n"))
    assert client.get("/categories").get_json()["names"] == ["ZOO", "STEM"]


@pytest.fixture
def fake_llm(monkeypatch):
    asked = []

    class FakeClient:
        def __init__(self, url, model):
            pass

        def define(self, category):
            asked.append(category)
            return f"{category} means business courses."

    monkeypatch.setattr(app_module, "OllamaClient", FakeClient)
    return asked


def test_go_drafts_a_definition_without_saving_it(client, fake_llm, folders):
    data = client.post("/categories/draft", data={"name": " business "}).get_json()
    assert data == {"name": "BUSINESS", "definition": "BUSINESS means business courses."}
    assert "BUSINESS" not in folders[1].read_text()


def test_save_writes_the_edited_definition_into_config_yaml(client, folders):
    config = folders[1]
    data = client.post("/categories", data={"name": "business",
                                            "definition": "  Edited: management, finance.  "})
    assert data.get_json()["categories"] == {"STEM": "Science and maths.",
                                             "BUSINESS": "Edited: management, finance."}
    text = config.read_text()
    # Everything outside the section is kept, comments included.
    assert text.startswith("# my settings\npdf-folder:")
    assert "log-folder: /nowhere  # keep me" in text
    assert "BUSINESS: 'Edited: management, finance.'" in text


def test_update_replaces_an_existing_definition_only(client, folders):
    r = client.post("/categories", data={"name": "stem", "definition": "Edited.", "replace": "1"})
    assert r.get_json()["categories"] == {"STEM": "Edited."}
    assert "STEM: Edited." in folders[1].read_text()
    r = client.post("/categories", data={"name": "LAW", "definition": "Law.", "replace": "1"})
    assert r.status_code == 404 and "LAW" not in folders[1].read_text()


def test_add_rejects_bad_existing_and_empty(client, fake_llm):
    assert client.post("/categories/draft", data={"name": "../x"}).status_code == 400
    assert client.post("/categories/draft", data={"name": "stem"}).status_code == 409
    assert client.post("/categories", data={"name": "LAW", "definition": " "}).status_code == 400
    assert fake_llm == []


def _student(work, name, courses):
    work.mkdir(exist_ok=True)
    (work / f"{name}.common.json").write_text(json.dumps({
        "student": {"name": name}, "source_file": f"{name}.pdf",
        "years": [{"terms": [{"term": "Fall", "courses": [
            {"code": c, "title": c, "grade": "A", "credits": 3, "gpa_4": 4.0,
             "counts_in_gpa": True} for c in courses]}]}]}))


def test_estimate_counts_distinct_unanswered_courses(client, tmp_path):
    _student(tmp_path / "work", "ann", ["MATH 1", "ENGL 1"])
    _student(tmp_path / "work", "bob", ["MATH 1", "PHYS 1"])
    data = client.get("/runs/estimate", query_string={"category": "STEM"}).get_json()
    assert data["students"] == 2 and data["courses"] == 3 and data["pending"] == 3
    assert data["seconds"] == round(3 * data["seconds_per_answer"])


def test_run_starts_in_the_background_and_reports_done(client, monkeypatch, folders):
    seen = []

    def fake_run(cfg, category, progress=None):
        seen.append(category)
        progress(1, 2)
        progress(2, 2)
        return folders[0] / "rank_stem_gpa.csv", None

    monkeypatch.setattr(app_module.llm_run, "run", fake_run)
    assert client.post("/runs", data={"category": "LAW"}).status_code == 404
    # The fake run may already have finished by the time the reply is built.
    assert client.post("/runs", data={"category": "stem"}).get_json()["status"] in ("running", "done")
    for _ in range(50):
        state = client.get("/runs/status").get_json()
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert seen == ["STEM"]
    assert state["status"] == "done" and state["done"] == 2 and state["folder"] == str(folders[0])
