"""Tests for the Flask front end.

The analysis itself is covered by ``test_pipeline``; what matters here is the
part the browser adds -- that both input paths reach the same run, that the
output folder is honoured, that a bad form field comes back as a message
instead of a traceback, and that downloads serve only what the last run wrote.
``/run`` answers the Run tab's fetch() with a JSON status, not a page.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("flask")

from gpa_ana.config import Config
from gpa_ana.web import create_app

CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


@pytest.fixture
def client():
    return create_app(CONFIG).test_client()


@pytest.fixture(scope="module")
def transcripts() -> Path:
    config = Config.load(CONFIG)
    if not config.pdf_folder.is_dir():
        pytest.skip(f"corpus not available: {config.pdf_folder}")
    return config.pdf_folder


def test_form_lists_every_available_category(client):
    body = client.get("/run").get_data(as_text=True)
    for name in ("STEM", "MEDICAL", "LAW"):
        assert f'value="{name}"' in body


def test_every_tab_renders_with_the_current_one_marked(client):
    run_page = client.get("/run").get_data(as_text=True)
    view_page = client.get("/view").get_data(as_text=True)
    help_page = client.get("/").get_data(as_text=True)  # the landing page
    assert client.get("/help").get_data(as_text=True) == help_page
    for body in (run_page, view_page, help_page):
        assert 'href="/run"' in body and 'href="/view"' in body and 'href="/"' in body
    assert '<a href="/run" class="active"' in run_page
    assert '<a href="/view" class="active"' in view_page
    assert '<a href="/" class="active"' in help_page
    assert "1. Run" in help_page and "2. View" in help_page
    assert 'id="status"' in run_page  # where the run reports back
    assert 'name="category"' in view_page


def test_view_shows_the_selected_category_as_a_table(client, transcripts, tmp_path):
    client.post(
        "/run",
        data={
            "pdf_folder": str(transcripts),
            "out_folder": str(tmp_path),
            "categories": ["STEM", "LAW"],
        },
    )
    # With no query, View opens on the folder the last run wrote.
    body = client.get("/view").get_data(as_text=True)
    assert f'value="{tmp_path}"' in body
    assert '<option value="LAW"' in body and '<option value="STEM"' in body
    assert '<option value="MEDICAL"' not in body  # not ranked into this folder

    import json
    stem = json.loads((tmp_path / "rank_stem_gpa.json").read_text())
    body = client.get(
        "/view", query_string={"folder": str(tmp_path), "category": "STEM"}
    ).get_data(as_text=True)
    assert "<h2>STEM</h2>" in body
    assert '<option value="STEM" selected' in body
    assert body.count('<tr class="student"') == len(stem["ranked"])
    assert stem["ranked"][0]["student"] in body
    assert "Gate" not in body
    assert 'id="row-tip"' in body and "Click the row to view student transcript details" in body


def test_clicking_a_student_loads_their_courses(client, transcripts, tmp_path):
    client.post(
        "/run",
        data={"pdf_folder": str(transcripts), "out_folder": str(tmp_path), "categories": ["LAW"]},
    )
    import json
    top = json.loads((tmp_path / "rank_law_gpa.json").read_text())["ranked"][0]
    assert f'data-file="{top["file"]}"' in client.get(
        "/view", query_string={"folder": str(tmp_path), "category": "LAW"}
    ).get_data(as_text=True)

    response = client.get(
        "/view/courses",
        query_string={"folder": str(tmp_path), "category": "LAW", "file": top["file"]},
    )
    assert response.status_code == 200
    record = response.get_json()
    assert record["student"] == top["student"]
    assert len(record["courses"]) == top["total_courses"]
    # Membership arrives under one name whatever the category, and agrees
    # with the ranking's own count.
    assert all("law" not in c for c in record["courses"])
    assert sum(c["in_category"] for c in record["courses"]) == top["category_courses"]


def test_dialog_links_the_original_pdf_for_a_folder_run(client, transcripts, tmp_path):
    client.post(
        "/run",
        data={"pdf_folder": str(transcripts), "out_folder": str(tmp_path), "categories": ["LAW"]},
    )
    import json
    assert json.loads((tmp_path / "sources.json").read_text()) == {
        "pdf_folder": str(transcripts.resolve())
    }
    top = json.loads((tmp_path / "rank_law_gpa.json").read_text())["ranked"][0]
    record = client.get(
        "/view/courses",
        query_string={"folder": str(tmp_path), "category": "LAW", "file": top["file"]},
    ).get_json()
    pdf = client.get(record["pdf_url"])
    assert pdf.status_code == 200
    assert pdf.mimetype == "application/pdf"
    assert pdf.data == (transcripts / top["file"]).read_bytes()
    assert "attachment" not in pdf.headers.get("Content-Disposition", "")  # shown inline


def test_uploaded_pdfs_are_kept_so_view_can_show_them(client, transcripts, tmp_path):
    source = sorted(transcripts.glob("*.pdf"))[0]
    client.post(
        "/run",
        data={
            "out_folder": str(tmp_path),
            "categories": ["STEM"],
            "pdfs": [(io.BytesIO(source.read_bytes()), source.name)],
        },
        content_type="multipart/form-data",
    )
    kept = tmp_path / "pdfs" / source.name
    assert kept.read_bytes() == source.read_bytes()
    pdf = client.get("/view/pdf", query_string={"folder": str(tmp_path), "file": source.name})
    assert pdf.status_code == 200


def test_pdf_lookup_without_a_record_falls_back_to_config(client, transcripts, tmp_path):
    """A CLI run writes no sources.json; its PDFs are in config.yaml's pdf-folder."""
    name = sorted(transcripts.glob("*.pdf"))[0].name
    assert client.get(
        "/view/pdf", query_string={"folder": str(tmp_path), "file": name}
    ).status_code == 200


@pytest.mark.parametrize(
    "file", ["no_such.pdf", "../config.yaml", "../../etc/passwd", "rank_stem_gpa.json", ""]
)
def test_pdf_endpoint_serves_only_pdfs_it_can_find(client, transcripts, tmp_path, file):
    response = client.get("/view/pdf", query_string={"folder": str(tmp_path), "file": file})
    assert response.status_code == 404


def test_ranking_columns_are_sortable(client, transcripts, tmp_path):
    client.post(
        "/run",
        data={"pdf_folder": str(transcripts), "out_folder": str(tmp_path), "categories": ["STEM"]},
    )
    body = client.get(
        "/view", query_string={"folder": str(tmp_path), "category": "STEM"}
    ).get_data(as_text=True)
    assert body.count('<button type="button" class="sort">') == 9  # every column
    assert 'class="table-wrap scroll"' in body  # rows scroll under a fixed header


@pytest.mark.parametrize(
    "file,category",
    [
        ("no_such_student.pdf", "LAW"),
        ("../../rank_law_gpa.json", "LAW"),  # only bare names inside extracted/
        ("", "LAW"),
        ("x.pdf", "MEDICAL"),  # not ranked into this folder
    ],
)
def test_courses_lookup_fails_cleanly(client, transcripts, tmp_path, file, category):
    client.post(
        "/run",
        data={"pdf_folder": str(transcripts), "out_folder": str(tmp_path), "categories": ["LAW"]},
    )
    response = client.get(
        "/view/courses",
        query_string={"folder": str(tmp_path), "category": category, "file": file},
    )
    assert response.status_code == 404
    assert response.get_json()["error"]


def test_view_reports_a_folder_without_rankings(client, tmp_path):
    body = client.get("/view", query_string={"folder": str(tmp_path)}).get_data(as_text=True)
    assert "no rankings in" in body
    assert "<table" not in body


def test_folder_run_ranks_only_the_selected_categories(client, transcripts, tmp_path):
    response = client.post(
        "/run",
        data={
            "pdf_folder": str(transcripts),
            "out_folder": str(tmp_path),
            "categories": ["STEM"],
            "min_courses": "4",
        },
    )
    assert response.status_code == 200
    status = response.get_json()
    assert status["ok"] is True
    assert status["parsed"] > 0
    assert [c["name"] for c in status["categories"]] == ["STEM"]  # MEDICAL not selected
    assert status["out_folder"] == str(tmp_path)
    assert (tmp_path / "rank_stem_gpa.csv").is_file()
    assert (tmp_path / "rank_stem_gpa.json").is_file()
    assert not (tmp_path / "rank_medical_gpa.csv").exists()


def test_uploaded_pdfs_are_ranked_like_a_folder(client, transcripts, tmp_path):
    pdfs = sorted(transcripts.glob("*.pdf"))[:2]
    response = client.post(
        "/run",
        data={
            "out_folder": str(tmp_path),
            "categories": ["STEM"],
            "pdfs": [(io.BytesIO(p.read_bytes()), p.name) for p in pdfs],
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["source"] == "2 uploaded file(s)"
    assert (tmp_path / "rank_stem_gpa.csv").is_file()


def test_output_folder_is_created_when_missing(client, transcripts, tmp_path):
    target = tmp_path / "does" / "not" / "exist"
    response = client.post(
        "/run",
        data={
            "pdf_folder": str(transcripts),
            "out_folder": str(target),
            "categories": ["STEM"],
        },
    )
    assert response.status_code == 200
    assert (target / "rank_stem_gpa.csv").is_file()


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"pdf_folder": "@CORPUS@"}, "select at least one category"),
        ({"categories": ["STEM"]}, "upload at least one PDF"),
        ({"pdf_folder": "/no/such/folder", "categories": ["STEM"]}, "not a folder"),
        ({"pdf_folder": "@CORPUS@", "categories": ["NOPE"]}, "no ruleset for category"),
    ],
)
def test_bad_input_returns_a_message_not_a_traceback(
    client, transcripts, tmp_path, payload, expected
):
    data = {"out_folder": str(tmp_path), **payload}
    if data.get("pdf_folder") == "@CORPUS@":
        data["pdf_folder"] = str(transcripts)
    response = client.post("/run", data=data)
    assert response.status_code == 400
    assert response.get_json()["ok"] is False
    assert expected in response.get_json()["error"]


def test_missing_output_path_is_rejected(client, transcripts):
    response = client.post(
        "/run", data={"pdf_folder": str(transcripts), "categories": ["STEM"]}
    )
    assert response.status_code == 400
    assert "an output path is required" in response.get_json()["error"]


def test_non_pdf_upload_is_refused(client, tmp_path):
    response = client.post(
        "/run",
        data={
            "out_folder": str(tmp_path),
            "categories": ["STEM"],
            "pdfs": [(io.BytesIO(b"text, not a transcript"), "notes.txt")],
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "not a PDF" in response.get_json()["error"]


def test_download_serves_only_what_the_last_run_wrote(client, transcripts, tmp_path):
    assert client.get("/download/stem.csv").status_code == 404  # nothing run yet

    client.post(
        "/run",
        data={
            "pdf_folder": str(transcripts),
            "out_folder": str(tmp_path),
            "categories": ["STEM"],
        },
    )
    assert client.get("/download/stem.csv").status_code == 200
    assert client.get("/download/stem.json").status_code == 200
    # LAW was not part of that run, so it is not downloadable.
    assert client.get("/download/law.csv").status_code == 404


def test_web_and_cli_agree_on_the_same_inputs(client, transcripts, tmp_path):
    """The browser is a front end, not a second implementation."""
    from gpa_ana.categories import load_ruleset
    from gpa_ana.layout import load_profiles
    from gpa_ana.service import analyse

    web_out, cli_out = tmp_path / "web", tmp_path / "cli"
    client.post(
        "/run",
        data={
            "pdf_folder": str(transcripts),
            "out_folder": str(web_out),
            "categories": ["STEM"],
            "min_courses": "4",
        },
    )
    analyse(
        sorted(transcripts.glob("*.pdf")),
        [load_ruleset("STEM")],
        load_profiles(),
        min_category_courses=4,
        out_folder=cli_out,
    )
    assert (web_out / "rank_stem_gpa.csv").read_text() == (
        cli_out / "rank_stem_gpa.csv"
    ).read_text()
