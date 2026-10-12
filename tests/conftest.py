"""Shared fixtures for the upload, llm_category, browser_view and install tests."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "install"))

# What `pdftotext -layout` prints for a transcript in the generated format.
TRANSCRIPT_TXT = """\
TEST UNIVERSITY
Springfield, USA
Office of the Registrar
Official Academic Transcript

STUDENT RECORD
Name: Ann Lee
Student ID: 12345
Major: Physics
Degree: Bachelor of Science (B.S.), conferred May 2025

YEAR 1: 2023-2024
Fall Semester 2023
Course       Title                    Grade   Credits   Points
PHYS 101     Mechanics                A       4.0       16.00
ENGL 101     Writing                  B+      3.0       9.90
ART 100      Drawing                  P       2.0       0.00
Credits: 9.0 Term GPA: 3.70 Cumulative Credits: 9.0 Cumulative GPA: 3.70
TEST UNIVERSITY — page 1

SUMMARY OF RECORD
Total Credits Earned: 9.0
Cumulative Grade Point Average: 3.70 (scale 4.0)

GRADING KEY
A = 4.0 A- = 3.7 B+ = 3.3 B = 3.0
P / F : Pass / Fail
"""


def common_json(work: Path, name: str, courses, institution="TEST UNIVERSITY") -> Path:
    """Write <work>/<name>.common.json with one term of (code, title, grade, credits, gpa_4)."""
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"{name}.common.json"
    path.write_text(json.dumps({
        "source_file": f"{name}.pdf",
        "institution": {"name": institution},
        "student": {"name": name.title(), "major": "Physics"},
        "years": [{"terms": [{"term": "Fall 2024", "courses": [
            {"code": code, "title": title, "grade": grade, "credits": credits, "gpa_4": gpa_4,
             "counts_in_gpa": gpa_4 is not None}
            for code, title, grade, credits, gpa_4 in courses]}]}],
    }), encoding="utf-8")
    return path


class FakeLLM:
    """Stands in for OllamaClient: a course is a member when its code starts with a prefix."""

    def __init__(self, url="http://fake", model="fake-model", members=("MATH", "PHYS")):
        self.url, self.model, self.members = url, model, members
        self.asked: list[str] = []
        self.defined: list[str] = []

    def define(self, category):
        self.defined.append(category)
        return f"{category} means science."

    def is_member(self, category, definition, code, title):
        self.asked.append(code)
        return code.startswith(self.members)


@pytest.fixture
def fake_llm():
    return FakeLLM()


class NoBrowser:
    """Stands in for the chatgpt and gemini steps: tests never open Chrome."""

    def __init__(self, cfg=None):
        pass

    def define(self, category):
        raise RuntimeError("no browser in tests")


class NoOllama(NoBrowser):
    def define(self, category):
        raise OSError("no ollama in tests")


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """The definer's steps fail at once unless a test fakes them: no test
    opens a browser or asks the real Ollama (fake_llm and friends replace it)."""
    from category_builder import definer

    for step in definer.BROWSER_STEPS:
        monkeypatch.setitem(definer.PROVIDERS, step, NoBrowser)
    monkeypatch.setitem(definer.PROVIDERS, "ollama", NoOllama)
