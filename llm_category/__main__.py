"""python -m llm_category --category STEM [--config config.yaml] [--limit N]"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .llm import OllamaClient
from .store import AnswerCache, Definitions

PROJECT_ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("llm_category")
DEFAULTS = {
    "model": "qwen2.5:3b",
    "ollama-url": "http://localhost:11434",
}
# Seconds per LLM answer when no run has been timed yet (the first full STEM
# run on this machine: 390 answers in about 85 minutes).
FALLBACK_SECONDS_PER_ANSWER = 13.0


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


@dataclass
class Student:
    name: str
    institution: str
    program: str
    file: str
    courses: list[Course] = field(default_factory=list)


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    llm_cfg = {**DEFAULTS, **(cfg.get("llm-category") or {})}
    return {
        "in": Path(cfg["work-folder"]).expanduser(),
        "log": Path(cfg.get("log-folder") or "log").expanduser(),
        "out": Path(cfg.get("out-folder", "out")).expanduser() / "llm_category",
        "min_courses": int(cfg.get("min-category-courses", 4)),
        "model": llm_cfg["model"],
        "url": llm_cfg["ollama-url"],
        "config": path,
    }


def setup_logging(log_folder: Path) -> None:
    """Console plus <log-folder>/llm_category.log (appended, timestamped)."""
    log_folder.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_folder / "llm_category.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(file_handler)
    log.addHandler(logging.StreamHandler(sys.stdout))
    log.setLevel(logging.INFO)


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


def weighted_gpa(courses: list[Course]) -> float | None:
    credits = sum(c.credits for c in courses)
    if not credits:
        return None
    return round(sum(c.gpa_4 * c.credits for c in courses) / credits, 4)


def classify(students, category, definition, cache, client, progress=None) -> None:
    """Fill in ``in_category``; ``progress(done, total)`` is called as answers arrive."""
    pending = {}
    for s in students:
        for c in s.courses:
            if cache.get(c.code, c.title) is None:
                pending.setdefault(cache.key(c.code, c.title), c)
    if pending:
        log.info("Asking %s about %d new course(s) (~10 s each on this machine; "
                 "answers are cached)", client.model, len(pending))
    if progress:
        progress(0, len(pending))
    for i, c in enumerate(pending.values(), start=1):
        t0 = time.monotonic()
        answer = client.is_member(category, definition, c.code, c.title)
        seconds = time.monotonic() - t0
        cache.put(c.code, c.title, answer, seconds)
        log.info("  [%d/%d] %-3s  %s - %s  (%.1fs)", i, len(pending),
                 "yes" if answer else "no", c.code, c.title, seconds)
        if progress:
            progress(i, len(pending))
    for s in students:
        for c in s.courses:
            c.in_category = cache.get(c.code, c.title)


def write_outputs(students, category, min_courses, out_dir: Path) -> tuple[Path, Path]:
    slug = category.lower()
    rows = []
    for s in students:
        members = [c for c in s.courses if c.in_category]
        rows.append({
            "student": s, "gpa": weighted_gpa(members), "overall": weighted_gpa(s.courses),
            "n": len(members), "total": len(s.courses),
            "gate": "pass" if len(members) >= min_courses else "fail",
        })
    # Ranked students first (by category GPA), then those below the course minimum.
    rows.sort(key=lambda r: (r["gate"] != "pass", -(r["gpa"] or 0), r["student"].name))

    out_dir.mkdir(parents=True, exist_ok=True)
    rank_csv = out_dir / f"rank_{slug}_gpa.csv"
    with rank_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "student", "institution", "program",
                    f"{category}_GPA", "all_courses_GPA",
                    f"{slug}_courses", "total_courses", f"{slug}_all_pct",
                    "gate", "file"])
        for position, r in enumerate(rows, start=1):
            s = r["student"]
            pct = round(100 * r["n"] / r["total"], 2) if r["total"] else 0
            w.writerow([position if r["gate"] == "pass" else "", s.name, s.institution,
                        s.program, "" if r["gpa"] is None else r["gpa"], r["overall"],
                        r["n"], r["total"], pct, r["gate"], s.file])

    courses_csv = out_dir / f"{slug}_courses.csv"
    with courses_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["student", "file", "term", "code", "title", "grade", "credits", "gpa_4",
                    f"is_{slug}"])
        for s in students:
            for c in s.courses:
                w.writerow([s.name, s.file, c.term, c.code, c.title, c.grade, c.credits, c.gpa_4,
                            "yes" if c.in_category else "no"])
    return rank_csv, courses_csv


def load_students(cfg: dict, limit: int | None = None) -> list[Student]:
    files = sorted(cfg["in"].glob("*.common.json"))
    if limit:
        files = files[:limit]
    if not files:
        raise RunError(f"No *.common.json files in {cfg['in']} -- run python3 -m upload first")
    return [load_student(p) for p in files]


def _cache(cfg: dict, category: str, definition: str) -> AnswerCache:
    return AnswerCache(cfg["out"] / f"cache_{category.lower()}.json", definition, cfg["model"])


def seconds_per_answer(cfg: dict) -> float:
    """Average LLM answer time over every timed cache in the output folder."""
    count, seconds = 0, 0.0
    for path in cfg["out"].glob("cache_*.json"):
        try:
            timing = json.loads(path.read_text(encoding="utf-8")).get("timing") or {}
        except (OSError, ValueError):
            continue
        count += timing.get("count", 0)
        seconds += timing.get("seconds", 0.0)
    return seconds / count if count else FALLBACK_SECONDS_PER_ANSWER


def estimate(cfg: dict, category: str, definition: str) -> dict:
    """How much LLM work a run of ``category`` would need right now."""
    students = load_students(cfg)
    cache = _cache(cfg, category, definition)
    keys = {cache.key(c.code, c.title) for s in students for c in s.courses}
    pending = sum(1 for k in keys if k not in cache.answers)
    per_answer = seconds_per_answer(cfg)
    return {"students": len(students), "courses": len(keys), "pending": pending,
            "seconds_per_answer": round(per_answer, 1),
            "seconds": round(pending * per_answer)}


def run(cfg: dict, category: str, limit: int | None = None, progress=None) -> tuple[Path, Path]:
    """Classify every student's courses for ``category`` and write the CSVs."""
    log.info("llm_category run: category %s, model %s", category, cfg["model"])
    students = load_students(cfg, limit)
    log.info("Loaded %d student(s) from %s", len(students), cfg["in"])

    client = OllamaClient(cfg["url"], cfg["model"])
    definitions = Definitions(cfg["config"])
    try:
        definition, created = definitions.get_or_create(category, client)
        if created:
            log.info("No definition for %s; the LLM wrote one and it was saved to %s:\n  %s",
                     category, cfg["config"], definition)
        cache = _cache(cfg, category, definition)
        if cache.reset:
            log.info("Definition or model changed since the last run; cached answers discarded.")
        classify(students, category, definition, cache, client, progress)
    except OSError as exc:  # URLError and socket timeouts are OSErrors
        raise RunError(f"Cannot reach Ollama at {cfg['url']}: {exc}") from None

    rank_csv, courses_csv = write_outputs(students, category, cfg["min_courses"], cfg["out"])
    log.info("Wrote %s", rank_csv)
    log.info("Wrote %s", courses_csv)
    return rank_csv, courses_csv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m llm_category", description=__doc__)
    ap.add_argument("--category", required=True, help="category name, e.g. STEM")
    ap.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    ap.add_argument("--limit", type=int, help="only process the first N students (for testing)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg["log"])
    try:
        run(cfg, args.category.upper(), args.limit)
    except RunError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
