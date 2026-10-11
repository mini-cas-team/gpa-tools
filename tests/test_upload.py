import json
import subprocess

import pytest

import upload.__main__ as upload_main
import upload.convert as convert_module
from upload.common.add_gpa import add_gpa
from upload.common.txt2common import convert

from conftest import TRANSCRIPT_TXT


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "ann.txt"
    path.write_text(TRANSCRIPT_TXT, encoding="utf-8")
    return path


def test_convert_reads_letterhead_and_student(transcript):
    doc = convert(transcript)
    assert doc["source_file"] == "ann.pdf"
    assert doc["institution"] == {"name": "TEST UNIVERSITY", "location": "Springfield, USA",
                                  "registrar_office": "Office of the Registrar"}
    assert doc["document_title"] == "Official Academic Transcript"
    st = doc["student"]
    assert (st["name"], st["student_id"], st["id_label"], st["major"]) == \
        ("Ann Lee", "12345", "Student ID", "Physics")
    assert (st["degree"], st["degree_abbreviation"], st["conferred"]) == \
        ("Bachelor of Science", "B.S.", "May 2025")


def test_convert_reads_terms_courses_and_summaries(transcript):
    doc = convert(transcript)
    assert doc["credit_unit"] == "Credits"
    [year] = doc["years"]
    assert (year["year"], year["label"]) == (1, "2023-2024")
    [term] = year["terms"]
    assert (term["season"], term["period_type"], term["year"]) == ("Fall", "semester", 2023)
    assert [(c["code"], c["title"], c["grade"], c["credits"]) for c in term["courses"]] == [
        ("PHYS 101", "Mechanics", "A", 4.0), ("ENGL 101", "Writing", "B+", 3.0),
        ("ART 100", "Drawing", "P", 2.0)]
    assert term["summary"] == {"credits": 9.0, "term_gpa": 3.70,
                               "cumulative_credits": 9.0, "cumulative_gpa": 3.70}
    assert doc["summary_of_record"]["total_credits_earned"] == 9.0
    assert doc["summary_of_record"]["gpa_scale"] == 4.0
    assert doc["grading_key"]["grade_points"]["B+"] == 3.3
    assert doc["grading_key"]["codes"]["P / F"] == "Pass / Fail"
    assert doc["warnings"] == []


def test_convert_warns_about_unknown_labels_and_lines(tmp_path):
    path = tmp_path / "odd.txt"
    path.write_text(TRANSCRIPT_TXT.replace("Student ID:", "Badge:")
                    .replace("Credits: 9.0 Term", "something odd\nCredits: 9.0 Term"),
                    encoding="utf-8")
    doc = convert(path)
    assert doc["student"]["student_id"] == "12345"  # second line falls back to student_id
    assert any("unknown student label 'Badge'" in w for w in doc["warnings"])
    assert "unparsed line: 'something odd'" in doc["warnings"]


def test_add_gpa_uses_the_key_and_skips_pass_fail(transcript):
    doc = add_gpa(convert(transcript))
    courses = {c["code"]: c for c in doc["years"][0]["terms"][0]["courses"]}
    assert (courses["PHYS 101"]["gpa"], courses["PHYS 101"]["gpa_4"]) == (4.0, 4.0)
    assert courses["PHYS 101"]["gpa_source"] == "grading_key"
    assert courses["ART 100"]["gpa"] is None and courses["ART 100"]["counts_in_gpa"] is False
    assert courses["ART 100"]["gpa_source"] == "non_gpa_grade"
    assert doc["warnings"] == []


def test_add_gpa_derives_missing_grades_and_checks_term_gpa(transcript):
    doc = convert(transcript)
    doc["grading_key"]["grade_points"]["B+"] = None
    doc["years"][0]["terms"][0]["summary"]["term_gpa"] = 2.0
    doc = add_gpa(doc)
    writing = doc["years"][0]["terms"][0]["courses"][1]
    assert writing["gpa"] == 3.3 and writing["gpa_source"] == "derived_from_points"
    assert any("derived from points/credits" in w for w in doc["warnings"])
    assert any("!= printed 2.0" in w for w in doc["warnings"])


@pytest.fixture
def fake_pdftotext(monkeypatch):
    """pdftotext that 'extracts' the bytes of the PDF as its text."""
    def run(cmd, **kwargs):
        pdf, txt = cmd[-2], cmd[-1]
        data = open(pdf, encoding="utf-8").read()
        if data == "broken":
            raise subprocess.CalledProcessError(1, cmd, stderr="Syntax Error\n")
        open(txt, "w", encoding="utf-8").write(data)

    monkeypatch.setattr(convert_module.shutil, "which", lambda name: "/bin/pdftotext")
    monkeypatch.setattr(convert_module.subprocess, "run", run)


def test_pdf_to_common_json_writes_txt_and_json(tmp_path, fake_pdftotext):
    pdf = tmp_path / "ann.pdf"
    pdf.write_text(TRANSCRIPT_TXT, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    doc = convert_module.pdf_to_common_json(pdf, work)
    assert (work / "ann.txt").read_text(encoding="utf-8") == TRANSCRIPT_TXT
    assert json.loads((work / "ann.common.json").read_text(encoding="utf-8")) == doc
    assert doc["years"][0]["terms"][0]["courses"][0]["gpa_4"] == 4.0


@pytest.mark.parametrize("content, message", [("   \n", "no text layer"), ("broken", "Syntax Error")])
def test_pdf_to_txt_reports_bad_pdfs(tmp_path, fake_pdftotext, content, message):
    pdf = tmp_path / "bad.pdf"
    pdf.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        convert_module.pdf_to_txt(pdf, tmp_path / "bad.txt")


def test_pdf_to_txt_needs_pdftotext(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_module.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="pdftotext not found"):
        convert_module.pdf_to_txt(tmp_path / "a.pdf", tmp_path / "a.txt")


def test_upload_converts_the_pdf_folder(tmp_path, fake_pdftotext, monkeypatch):
    monkeypatch.setattr(upload_main, "log", upload_main.logging.getLogger("upload-test"))
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "ann.pdf").write_text(TRANSCRIPT_TXT, encoding="utf-8")
    (pdfs / "bad.pdf").write_text("broken", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(f"pdf-folder: {pdfs}\nwork-folder: {tmp_path / 'work'}\n"
                      f"log-folder: {tmp_path / 'log'}\n", encoding="utf-8")
    assert upload_main.main(["--config", str(config)]) == 1  # one PDF failed
    assert (tmp_path / "work" / "ann.common.json").is_file()
    assert not (tmp_path / "work" / "bad.common.json").exists()
    log_text = (tmp_path / "log" / "upload.log").read_text(encoding="utf-8")
    assert "bad.pdf: Syntax Error" in log_text and "1 converted, 1 failed" in log_text
