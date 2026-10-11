"""The jev category builder: TypeSafe's Jev model decides course membership.

Reads the per-student ``*.common.json`` in work-folder, asks Jev for each
distinct course how likely it is to belong to the category (scores are
cached), and writes the builder CSVs into <work-folder>/jev/.  A course is in
the category when its score is above the threshold.  Settings come from
config.yaml; the API key itself lives in the environment variable named by
api-key-env, never in config.yaml:

    category-builder:
      jev:
        model: jev-1.13.0
        api-url: https://api.typesafe.ai/v1/systemone
        api-key-env: GPA_TOOLS_KEY
        threshold: 0.5
        batch-size: 20
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import yaml

import category_builder
from category_builder import definer
from category_builder.common import RunError, load_students, write_outputs

from .client import JevClient, JevError
from .store import ScoreCache

NAME = "jev"
log = logging.getLogger("jev_category")
DEFAULTS = {
    "model": "jev-1.13.0",
    "api-url": "https://api.typesafe.ai/v1/systemone",
    "api-key-env": "GPA_TOOLS_KEY",
    "threshold": 0.5,
    "batch-size": 20,
}
# Seconds per course when no run has been timed yet (a 20-course Playground
# request took about 0.2 s; network time on top of that).
FALLBACK_SECONDS_PER_ANSWER = 0.1
# For the cost line in the log only: https://docs.typesafe.ai/models.md
USD_PER_MILLION_INPUT_TOKENS = 0.042


def load_config(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    jev_cfg = {**DEFAULTS, **category_builder.settings(raw, NAME)}
    return {
        "in": category_builder.work_folder(raw),
        "folder": category_builder.folder(raw, NAME),
        "log": Path(raw.get("log-folder") or "log").expanduser(),
        "model": jev_cfg["model"],
        "url": jev_cfg["api-url"],
        "key_env": jev_cfg["api-key-env"],
        "threshold": float(jev_cfg["threshold"]),
        "batch_size": max(1, int(jev_cfg["batch-size"])),
        "config": path,
        "raw": raw,
    }


def setup_logging(log_folder: Path) -> None:
    """Console plus <log-folder>/jev_category.log (appended, timestamped)."""
    log_folder.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_folder / "jev_category.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(file_handler)
    log.addHandler(logging.StreamHandler(sys.stdout))
    log.setLevel(logging.INFO)


def classify(students, category, definition, cache, client, threshold=0.5, batch_size=20,
             progress=None) -> None:
    """Fill in ``score`` and ``in_category``; ``progress(done, total)`` is called per request."""
    pending = {}
    for s in students:
        for c in s.courses:
            if cache.get(c.code, c.title) is None:
                pending.setdefault(cache.key(c.code, c.title), (c.code, c.title))
    courses = list(pending.values())
    if courses:
        log.info("Asking %s about %d new course(s), %d per request (scores are cached)",
                 client.model, len(courses), batch_size)
    if progress:
        progress(0, len(courses))
    tokens = 0
    for start in range(0, len(courses), batch_size):
        batch = courses[start:start + batch_size]
        t0 = time.monotonic()
        scores, usage = client.scores(category, definition, batch)
        seconds = time.monotonic() - t0
        cache.put_many(dict(zip(batch, scores)), seconds)
        tokens += usage.get("input_tokens") or 0
        for (code, title), score in zip(batch, scores):
            log.info("  %-3s %.2f  %s - %s", "yes" if score > threshold else "no", score, code, title)
        log.info("  [%d/%d] answered (%.1fs)", start + len(batch), len(courses), seconds)
        if progress:
            progress(start + len(batch), len(courses))
    if courses:
        log.info("Used %d input tokens (about $%.4f at $%s per million)", tokens,
                 tokens * USD_PER_MILLION_INPUT_TOKENS / 1e6, USD_PER_MILLION_INPUT_TOKENS)
    for s in students:
        for c in s.courses:
            c.score = cache.get(c.code, c.title)
            c.in_category = c.score > threshold


def _cache(cfg: dict, category: str, definition: str) -> ScoreCache:
    return ScoreCache(cfg["folder"] / f"cache_{category.lower()}.json", definition, cfg["model"])


def seconds_per_answer(cfg: dict) -> float:
    """Average time per course over every timed cache in the builder folder."""
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
    """How much Jev work a run of ``category`` would need right now."""
    students = load_students(cfg["in"])
    cache = _cache(cfg, category, definition)
    keys = {cache.key(c.code, c.title) for s in students for c in s.courses}
    pending = sum(1 for k in keys if k not in cache.answers)
    per_answer = seconds_per_answer(cfg)
    return {"students": len(students), "courses": len(keys), "pending": pending,
            "seconds_per_answer": round(per_answer, 2),
            "seconds": round(pending * per_answer)}


def api_key(cfg: dict) -> str:
    key = os.environ.get(cfg["key_env"], "").strip()
    if not key:
        raise RunError(f"The TypeSafe API key is not set: export {cfg['key_env']}=<key> "
                       f"(api-key-env in config.yaml names this variable)")
    return key


def run(cfg: dict, category: str, limit: int | None = None, progress=None) -> Path:
    """Score every student's courses for ``category`` and write the CSVs."""
    log.info("jev builder run: category %s, model %s, threshold %s", category, cfg["model"],
             cfg["threshold"])
    students = load_students(cfg["in"], limit)
    log.info("Loaded %d student(s) from %s", len(students), cfg["in"])

    client = JevClient(cfg["url"], cfg["model"], api_key(cfg))
    try:
        definition, drafted = definer.definition_for(cfg["config"], cfg["raw"], category)
    except OSError as exc:  # drafting a missing definition calls category-definer's model
        raise RunError(f"No definition for {category} and category-definer "
                       f"({definer.describe(definer.load_config(cfg['raw']))}) failed: {exc}") from None
    if drafted:
        log.info("No definition for %s; category-definer drafted one and it was saved "
                 "to %s:\n  %s", category, cfg["config"], definition)
    cache = _cache(cfg, category, definition)
    if cache.reset:
        log.info("Definition or model changed since the last run; cached scores discarded.")
    try:
        classify(students, category, definition, cache, client, cfg["threshold"],
                 cfg["batch_size"], progress)
    except JevError as exc:
        raise RunError(f"TypeSafe API at {cfg['url']}: {exc}") from None
    except OSError as exc:  # URLError and socket timeouts are OSErrors
        raise RunError(f"Cannot reach TypeSafe at {cfg['url']}: {exc}") from None

    gpa_csv, courses_csv = write_outputs(students, category, cfg["folder"])
    log.info("Wrote %s", gpa_csv)
    log.info("Wrote %s", courses_csv)
    return gpa_csv
