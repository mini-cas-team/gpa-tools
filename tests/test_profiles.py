"""profile.profile_load and browser_view's Profiles pages."""

import io
import json

import pytest

from browser_view.app import create_app
from profile import enrich, profile_load


CONFIG = "input-folder: {tmp}/in\nwork-folder: {tmp}/work\n"


@pytest.fixture
def folder(tmp_path):
    """<work-folder>/personal with ann.json and bob.json (what Upload makes)."""
    folder = tmp_path / "work" / "personal"
    folder.mkdir(parents=True)
    (folder / "ann.json").write_text(json.dumps(
        {"id_candidate_application": "ann", "name": "Ann Lee", "age": 20, "home": {"city": "Pune"},
         "major_residence": {"residence_country": "India", "residence_state": "Maharashtra",
                               "residence_city": "Pune"},
         "second_residence": None}))
    (folder / "bob.json").write_text(json.dumps({"name": "Bob Ray", "sex": "M"}))
    (folder / "notes.txt").write_text("not a profile")
    return folder


@pytest.fixture
def inbox(tmp_path):
    """<input-folder>/personal, where the .profile files are."""
    inbox = tmp_path / "in" / "personal"
    inbox.mkdir(parents=True)
    return inbox


@pytest.fixture
def client(tmp_path, folder):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(tmp=tmp_path))
    return create_app(config).test_client()


def test_load_keys_every_json_by_file_name(folder):
    profiles = profile_load.load(folder)
    assert profiles is profile_load.PROFILES
    assert set(profiles) == {"ann.json", "bob.json"}
    assert profiles["ann.json"]["home"] == {"city": "Pune"}
    assert profile_load.ERRORS == {}


def test_load_skips_bad_files_and_replaces_what_it_held(folder):
    profile_load.load(folder)
    (folder / "bob.json").unlink()
    (folder / "bad.json").write_text("{nope")
    (folder / "list.json").write_text("[1, 2]")
    assert set(profile_load.load(folder)) == {"ann.json"}
    assert set(profile_load.ERRORS) == {"bad.json", "list.json"}


def test_load_config_reads_the_personal_work_folder(tmp_path, folder):
    config = tmp_path / "c.yaml"
    config.write_text(CONFIG.format(tmp=tmp_path))
    assert set(profile_load.load_config(config)) == {"ann.json", "bob.json"}
    cfg = {"input-folder": "/x", "work-folder": "/x/work"}
    assert profile_load.personal_folder(cfg).as_posix() == "/x/personal"
    assert profile_load.personal_work_folder(cfg).as_posix() == "/x/work/personal"
    assert profile_load.load_cfg({}) == {}  # no work-folder: no profiles


def test_view_shows_one_row_per_profile_with_nested_fields(client):
    body = client.get("/profiles/view").get_data(as_text=True)
    assert 'href="/profiles/view" class="sub active" aria-current="page">Profiles' in body
    assert "Ann Lee" in body and "Bob Ray" in body and "Pune" in body
    assert ">home.city</button>" in body
    # each residence as three columns, M. (major) and S. (second), explained on hover
    for col in ("M.country", "M.state", "M.city", "S.country", "S.state", "S.city"):
        assert f">{col}</button>" in body
    assert '<th title="M = major residence: the state the applicant mainly lives in"><button type="button" class="sort">M.state</button></th>' in body
    assert '<th title="S = second residence: the city the applicant also lives in">' in body
    assert "<td>Maharashtra</td>" in body and "residence</button>" not in body and "residence_city" not in body
    assert ">file</button>" not in body and "id_candidate_application" not in body and "ann.json" not in body


def test_view_rows_highlight_and_the_name_opens_the_student_dialog(client):
    body = client.get("/profiles/view").get_data(as_text=True)
    rows = body.split("<tbody>")[1].split("</tbody>")[0]
    assert '<td><a href="#" class="student-link" data-file="ann.pdf">Ann Lee</a></td>' in rows
    assert '<td><a href="#" class="student-link" data-file="bob.pdf">Bob Ray</a></td>' in rows
    assert 'id="student-dialog"' in body and "studentRows(body," in body


def test_view_with_no_profiles_says_so(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"input-folder: {tmp_path / 'none'}\nwork-folder: {tmp_path / 'none'}\n")
    body = create_app(config).test_client().get("/profiles/view").get_data(as_text=True)
    assert "no profiles in" in body


def test_upload_reads_profile_files_and_saves_json(client, folder, inbox):
    page = client.get("/profiles/upload").get_data(as_text=True)
    assert 'href="/profiles/upload" class="sub active" aria-current="page">Profiles' in page
    assert 'accept=".profile"' in page
    r = client.post("/profiles/upload", data={"profiles": [
        (io.BytesIO(b'{"name": "Cy"}'), "../cy.profile")]}, content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.get_json() == {"status": "done", "saved": ["cy.json"], "loaded": 3, "errors": [],
                            "filled": {"cy.json": ["institution", "program"]},
                            "unmatched": ["cy.json"]}
    assert (inbox / "cy.profile").read_bytes() == b'{"name": "Cy"}'  # the source is kept as it came
    assert (folder / "cy.json").is_file() and not (inbox / "cy.json").exists()
    assert not (folder / "cy.profile").exists()
    assert profile_load.PROFILES["cy.json"] == {"name": "Cy", "institution": None, "program": None}


def test_upload_with_no_files_converts_every_profile_in_the_folder(client, folder, inbox):
    (folder / "ann.json").unlink()
    (inbox / "ann.profile").write_text('{"name": "Ann New"}')
    (inbox / "dee.profile").write_text('{"name": "Dee"}')
    r = client.post("/profiles/upload", data={}, content_type="multipart/form-data")
    assert r.get_json()["saved"] == ["ann.json", "dee.json"] and r.get_json()["loaded"] == 3
    assert json.loads((folder / "ann.json").read_text()) == {"name": "Ann New", "institution": None,
                                                             "program": None}


def test_upload_with_no_files_and_no_profiles_says_so(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(tmp=tmp_path))
    r = create_app(config).test_client().post("/profiles/upload", data={},
                                              content_type="multipart/form-data")
    assert r.status_code == 400 and "no .profile files" in r.get_json()["error"]


def test_upload_rejects_non_profiles_and_writes_nothing(client, folder, inbox):
    for name, data in (("x.json", b"{}"), ("x.txt", b"{}"), ("x.profile", b"{bad"),
                       ("x.profile", b"[1]")):
        r = client.post("/profiles/upload", data={"profiles": [
            (io.BytesIO(b'{"ok": 1}'), "ok.profile"), (io.BytesIO(data), name)]},
            content_type="multipart/form-data")
        assert r.status_code == 400 and r.get_json()["error"]
    for name in ("ok.profile", "ok.json", "x.profile", "x.json"):
        assert not (folder / name).exists() and not (inbox / name).exists()


def test_transcript_fields_by_student_name(tmp_path):
    (tmp_path / "a.common.json").write_text(json.dumps(
        {"student": {"name": "Ann Lee", "major": "Biology"},
         "institution": {"name": "STATE U", "location": None}}))
    (tmp_path / "b.common.json").write_text(json.dumps({"student": {"name": "Ann Lee", "major": "Art"},
                                                        "institution": {"name": "LATER U"}}))
    (tmp_path / "c.common.json").write_text(json.dumps({"student": {"name": "Bob"}}))
    (tmp_path / "d.common.json").write_text("{bad")
    assert enrich.transcript_fields(tmp_path) == {
        "Ann Lee": {"institution": "STATE U", "program": "Biology"},  # the first transcript
        "Bob": {"institution": None, "program": None}}
    assert enrich.transcript_fields(None) == {}


def test_add_missing_only_adds_what_the_profile_lacks():
    known = {"Ann Lee": {"institution": "STATE U", "program": "Biology"}}
    ann = {"name": "Ann Lee"}
    assert enrich.add_missing(ann, known) == ["institution", "program"]
    assert ann == {"name": "Ann Lee", "institution": "STATE U", "program": "Biology"}
    kept = {"name": "Ann Lee", "institution": "MINE"}
    assert enrich.add_missing(kept, known) == ["program"] and kept["institution"] == "MINE"
    empty = {"name": "Ann Lee", "institution": None, "program": None}  # has both: left alone
    assert enrich.add_missing(empty, known) == [] and empty["program"] is None
    nobody = {"name": "Cy"}
    assert enrich.add_missing(nobody, known) == ["institution", "program"]
    assert nobody["program"] is None and not enrich.has_transcript(nobody, known)


def test_upload_fills_in_from_the_matching_transcript(tmp_path, folder, inbox):
    work = tmp_path / "work" / "transcripts"
    work.mkdir()
    (work / "ann.common.json").write_text(json.dumps(
        {"student": {"name": "Ann Lee", "major": "Biology"}, "institution": {"name": "STATE U"}}))
    (work / "bob.common.json").write_text(json.dumps(
        {"student": {"name": "Bob Ray", "major": "Law"}, "institution": {"name": "BOB U"}}))
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(tmp=tmp_path))
    client = create_app(config).test_client()
    (inbox / "ann.profile").write_text(json.dumps({"name": "Ann Lee", "age": 20}))
    (inbox / "bob.profile").write_text(json.dumps({"name": "Bob Ray", "institution": "OWN U"}))
    (inbox / "cy.profile").write_text(json.dumps({"name": "Cy"}))
    data = client.post("/profiles/upload", data={}, content_type="multipart/form-data").get_json()
    assert data["filled"] == {"ann.json": ["institution", "program"], "bob.json": ["program"],
                              "cy.json": ["institution", "program"]}
    assert data["unmatched"] == ["cy.json"]
    assert json.loads((folder / "ann.json").read_text()) == {
        "name": "Ann Lee", "age": 20, "institution": "STATE U", "program": "Biology"}
    assert json.loads((folder / "bob.json").read_text()) == {
        "name": "Bob Ray", "institution": "OWN U", "program": "Law"}
    assert "program" not in json.loads((inbox / "ann.profile").read_text())


def test_student_dialog_has_the_students_profile(tmp_path, folder):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(tmp=tmp_path))
    client = create_app(config).test_client()
    data = client.get("/transcripts/view/student", query_string={"file": "ann.pdf"}).get_json()
    # matched by the file key; flattened like View → Profiles, id_* left out
    assert data["profile"] == [["name", "Ann Lee"], ["age", 20], ["home.city", "Pune"],
                               ["M.country", "India"], ["M.state", "Maharashtra"], ["M.city", "Pune"],
                               ["S.country", None], ["S.state", None], ["S.city", None]]
    nobody = client.get("/transcripts/view/student", query_string={"file": "cy.pdf"}).get_json()
    assert nobody["profile"] is None
    (folder / "bad.json").write_text("{nope")
    assert client.get("/transcripts/view/student",
                      query_string={"file": "bad.pdf"}).get_json()["profile"] is None


def test_student_dialog_has_a_profile_tab(tmp_path, folder):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(tmp=tmp_path))
    client = create_app(config).test_client()
    body = client.get("/letters/view").get_data(as_text=True)  # always has the student dialog
    assert 'id="tab-profile" aria-controls="panel-profile" aria-selected="false">Profile</button>' in body
    assert 'id="panel-profile" role="tabpanel" aria-labelledby="tab-profile" hidden' in body
