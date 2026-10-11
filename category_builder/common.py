"""Reading the common-format transcripts and writing the builder CSVs.

Every builder reads its input with ``load_students`` and writes its output
with ``write_outputs``, so the CSV layout -- what the View tab, out_builder
and other applications read -- is the same whichever builder made it.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


class RunError(Exception):
    """A problem that stops a run, with a message for the operator."""


@dataclass
class Course:
    term: str
    code: str
    title: str
    grade: str
    credits: float
    gpa_4: float
    in_category: bool | None = None
    score: float | None = None  # builders that score membership 0-1 (jev) set this


@dataclass
class Student:
    name: str
    institution: str
    program: str
    file: str
    courses: list[Course] = field(default_factory=list)


def load_student(path: Path) -> Student:
    doc = json.loads(path.read_text(encoding="utf-8"))
    st = doc.get("student") or {}
    inst = doc.get("institution") or {}
    student = Student(
        name=st.get("name") or path.name.removesuffix(".common.json"),
        institution=(inst.get("name") if isinstance(inst, dict) else inst) or "",
        program=st.get("major") or "",
        file=doc.get("source_file") or path.name,
    )
    for year in doc.get("years") or []:
        for term in year.get("terms") or []:
            term_name = term.get("term") or ""
            for c in term.get("courses") or []:
                # Only courses that count toward GPA and have a 0-4 value can rank.
                if not c.get("counts_in_gpa") or c.get("gpa_4") is None or not c.get("credits"):
                    continue
                student.courses.append(
                    Course(term_name, c.get("code") or "", c.get("title") or "", c.get("grade") or "",
                           float(c["credits"]), float(c["gpa_4"]))
                )
    return student


def load_students(work: Path, limit: int | None = None) -> list[Student]:
    files = sorted(work.glob("*.common.json"))
    if limit:
        files = files[:limit]
    if not files:
        raise RunError(f"No *.common.json files in {work} -- run python3 -m upload first")
    return [load_student(p) for p in files]


def weighted_gpa(courses: list[Course]) -> float | None:
    credits = sum(c.credits for c in courses)
    if not credits:
        return None
    return round(sum(c.gpa_4 * c.credits for c in courses) / credits, 4)


def gpa_csv_name(category: str) -> str:
    return f"{category.lower()}_gpa.csv"


def courses_csv_name(category: str) -> str:
    return f"{category.lower()}_courses.csv"


def write_outputs(students: list[Student], category: str, folder: Path) -> tuple[Path, Path]:
    """<cat>_gpa.csv and <cat>_courses.csv in ``folder``; ``in_category`` must be set."""
    slug = category.lower()
    rows = []
    for s in students:
        members = [c for c in s.courses if c.in_category]
        rows.append((s, weighted_gpa(members), weighted_gpa(s.courses), len(members),
                     len(s.courses)))
    # No ranking here; highest category GPA first just makes the file easy to read.
    rows.sort(key=lambda r: (r[1] is None, -(r[1] or 0), r[0].name))

    folder.mkdir(parents=True, exist_ok=True)
    gpa_csv = folder / gpa_csv_name(category)
    with gpa_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["student", "institution", "program",
                    f"{category}_GPA", "all_courses_GPA",
                    f"{slug}_courses", "total_courses", f"{slug}_all_pct", "file"])
        for s, gpa, overall, n, total in rows:
            pct = round(100 * n / total, 2) if total else 0
            w.writerow([s.name, s.institution, s.program, "" if gpa is None else gpa,
                        "" if overall is None else overall, n, total, pct, s.file])

    courses_csv = folder / courses_csv_name(category)
    with courses_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["student", "file", "term", "code", "title", "grade", "credits", "gpa_4",
                    f"is_{slug}", f"{slug}_score"])
        for s in students:
            for c in s.courses:
                w.writerow([s.name, s.file, c.term, c.code, c.title, c.grade, c.credits, c.gpa_4,
                            "yes" if c.in_category else "no",
                            "" if c.score is None else round(c.score, 4)])
    return gpa_csv, courses_csv
