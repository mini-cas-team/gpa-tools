"""Shared fixtures for the upload, category builder, browser_view and install tests."""

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


class FakeDefiner:
    """Stands in for a definer step (a web chat): drafts "<CAT> means science."."""

    def __init__(self):
        self.defined: list[str] = []

    def define(self, category):
        self.defined.append(category)
        return f"{category} means science."


class NoBrowser:
    """Stands in for the chatgpt and gemini steps: tests never open Chrome."""

    def __init__(self, cfg=None):
        pass

    def define(self, category):
        raise RuntimeError("no browser in tests")


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """The definer's steps fail at once unless a test fakes them: no test opens a browser."""
    from category_builder import definer

    for step in definer.BROWSER_STEPS:
        monkeypatch.setitem(definer.PROVIDERS, step, NoBrowser)


@pytest.fixture(autouse=True)
def gpa_tools_home(tmp_path, monkeypatch):
    """~/.gpa-tools (this laptop's cloud token) is a temporary folder: no test reads or
    writes the real one."""
    home = tmp_path / "gpa-tools-home"
    monkeypatch.setenv("GPA_TOOLS_HOME", str(home))
    return home


class FakeCloud:
    """The cloud server's calls for one account (an email), in memory, as gpa-tools-cloud
    answers them: shared definitions (an AI's first text wins), this email's private ones
    (Update), n_limit new categories and private texts together, the 409 for a text that is not the email's, and the lock
    (``in_use``: another computer has the account -> 423)."""

    url = "https://c.test"
    model = "jev-server"

    def __init__(self, shared=None, private=None, n_limit=3, down=False):
        self.shared = dict(shared or {})
        self.private = dict(private or {})
        self.ai = {c: "gemini" for c in self.shared}
        self.n_limit, self.n_used = n_limit, 0
        self.down, self.in_use = down, None
        self.rev = 1
        self.created, self.asked, self.calls = [], [], []

    def _check(self, name):
        from jev_category.client import CloudError

        self.calls.append(name)
        if self.down:
            raise OSError("cloud unreachable")
        if self.in_use:
            raise CloudError("HTTP 423: this account is in use on pc2", 423,
                             {"in_use": {"label": "pc2", "until": "2026-11-22T11:12:00.000Z"}})

    def _n(self):
        left = None if self.n_limit is None else max(0, self.n_limit - self.n_used)
        return {"limit": self.n_limit, "used": self.n_used, "left": left}

    def entry(self, cat):
        if cat in self.private:
            return {"definition": self.private[cat], "source": "private", "ai": self.ai.get(cat)}
        if cat in self.shared:
            return {"definition": self.shared[cat], "source": "shared", "ai": self.ai.get(cat)}
        return None

    def listing(self, since=None):
        self._check("listing")
        if since == str(self.rev):
            return {"rev": str(self.rev), "unchanged": True, "n": self._n()}
        entries = {c: self.entry(c) for c in sorted(set(self.shared) | set(self.private))}
        return {"rev": str(self.rev), "definitions": {c: e["definition"] for c, e in entries.items()},
                "entries": entries, "n": self._n()}

    def connect(self, since=None):
        reply = self.listing(since)
        self.calls[-1] = "connect"
        return {"email": "ann@school.edu", "label": "pc1", "active": {"label": "pc1"}, **reply}

    def disconnect(self):
        self.calls.append("disconnect")
        return True

    def definitions(self):
        return self.listing()["definitions"]

    def definition(self, category):
        self._check("definition")
        e = self.entry(category)
        return e and e["definition"]

    def _spend(self):
        from jev_category.client import CloudError

        if self.n_limit is not None and self.n_used >= self.n_limit:
            raise CloudError(f"HTTP 429: no Add or Update left for ann@school.edu: {self.n_used} of "
                             f"{self.n_limit} used", 429, {"n": self._n()})
        self.n_used += 1

    def create_definition(self, category, definition, source=None):
        self._check("create")
        if self.entry(category):
            return {"category": category, **self.entry(category), "created": False, "n": self._n()}
        self._spend()
        self.shared[category], self.ai[category] = definition.strip(), source
        self.created.append(category)
        self.rev += 1
        return {"category": category, **self.entry(category), "created": True, "n": self._n()}

    def set_private(self, category, definition, base):
        from jev_category.client import CloudError

        self._check("set_private")
        current = self.entry(category)
        if current is None:
            raise CloudError(f"HTTP 404: no definition for {category}", 404, {})
        if base is not None and base.strip() != current["definition"]:
            raise CloudError(f"HTTP 409: {category}'s definition was changed on the server", 409,
                             {"category": category, **current})
        if definition.strip() == current["definition"]:
            return {"category": category, **current, "changed": False, "n": self._n()}
        self._spend()
        self.private[category] = definition.strip()
        self.rev += 1
        return {"category": category, **self.entry(category), "changed": True, "n": self._n()}

    def reset_private(self, category):
        self._check("reset_private")
        self.private.pop(category, None)
        self.rev += 1
        return {"category": category, **(self.entry(category) or {"definition": None, "source": None}),
                "n": self._n()}

    def scores(self, category, definition, courses):
        from jev_category.client import CloudError

        self._check("scores")
        current = self.entry(category)
        if current is None or current["definition"] != definition.strip():
            raise CloudError(f"HTTP 409: {category}'s definition on the server is not the one sent", 409,
                             {"category": category, **(current or {"definition": None})})
        self.asked.append(definition)
        return [0.9] * len(courses), {"cached": 0, "asked": len(courses)}

    def rank_letter(self, letter):
        self._check("rank_letter")
        return "good", 0.8
