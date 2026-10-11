"""The llm category builder: a local LLM (Ollama) decides course membership.

Reads the per-student ``*.common.json`` in work-folder, asks the LLM whether
each distinct course belongs to the category (answers are cached), and writes
the builder CSVs into <work-folder>/llm/.  Settings come from config.yaml:

    category-builder:
      llm:
        model: qwen2.5:3b
        ollama-url: http://localhost:11434
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import yaml

import category_builder
from category_builder import definer
from category_builder.common import RunError, load_students, write_outputs

from .llm import OllamaClient
from .store import AnswerCache

NAME = "llm"
log = logging.getLogger("llm_category")
DEFAULTS = {
    "model": "qwen2.5:3b",
    "ollama-url": "http://localhost:11434",
}
# Seconds per LLM answer when no run has been timed yet (the first full STEM
# run on this machine: 390 answers in about 85 minutes).
FALLBACK_SECONDS_PER_ANSWER = 13.0


def load_config(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    llm_cfg = {**DEFAULTS, **category_builder.settings(raw, NAME)}
    return {
        "in": category_builder.work_folder(raw),
        "folder": category_builder.folder(raw, NAME),
        "log": Path(raw.get("log-folder") or "log").expanduser(),
        "model": llm_cfg["model"],
        "url": llm_cfg["ollama-url"],
        "config": path,
        "raw": raw,
    }


def setup_logging(log_folder: Path) -> None:
    """Console plus <log-folder>/llm_category.log (appended, timestamped)."""
    log_folder.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_folder / "llm_category.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(file_handler)
    log.addHandler(logging.StreamHandler(sys.stdout))
    log.setLevel(logging.INFO)


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


def _cache(cfg: dict, category: str, definition: str) -> AnswerCache:
    return AnswerCache(cfg["folder"] / f"cache_{category.lower()}.json", definition, cfg["model"])


def seconds_per_answer(cfg: dict) -> float:
    """Average LLM answer time over every timed cache in the builder folder."""
    count, seconds = 0, 0.0
    for path in cfg["folder"].glob("cache_*.json"):
        try:
            timing = json.loads(path.read_text(encoding="utf-8")).get("timing") or {}
        except (OSError, ValueError):
            continue
        count += timing.get("count", 0)
        seconds += timing.get("seconds", 0.0)
    return seconds / count if count else FALLBACK_SECONDS_PER_ANSWER


def estimate(cfg: dict, category: str, definition: str) -> dict:
    """How much LLM work a run of ``category`` would need right now."""
    students = load_students(cfg["in"])
    cache = _cache(cfg, category, definition)
    keys = {cache.key(c.code, c.title) for s in students for c in s.courses}
    pending = sum(1 for k in keys if k not in cache.answers)
    per_answer = seconds_per_answer(cfg)
    return {"students": len(students), "courses": len(keys), "pending": pending,
            "seconds_per_answer": round(per_answer, 1),
            "seconds": round(pending * per_answer)}


def run(cfg: dict, category: str, limit: int | None = None, progress=None) -> Path:
    """Classify every student's courses for ``category`` and write the CSVs."""
    log.info("llm builder run: category %s, model %s", category, cfg["model"])
    students = load_students(cfg["in"], limit)
    log.info("Loaded %d student(s) from %s", len(students), cfg["in"])

    client = OllamaClient(cfg["url"], cfg["model"])
    try:
        definition, drafted = definer.definition_for(cfg["config"], cfg["raw"], category)
        if drafted:
            log.info("No definition for %s; category-definer drafted one and it was saved "
                     "to %s:\n  %s", category, cfg["config"], definition)
        cache = _cache(cfg, category, definition)
        if cache.reset:
            log.info("Definition or model changed since the last run; cached answers discarded.")
        classify(students, category, definition, cache, client, progress)
    except OSError as exc:  # URLError and socket timeouts are OSErrors
        raise RunError(f"Cannot reach Ollama at {cfg['url']}: {exc}") from None

    gpa_csv, courses_csv = write_outputs(students, category, cfg["folder"])
    log.info("Wrote %s", gpa_csv)
    log.info("Wrote %s", courses_csv)
    return gpa_csv
