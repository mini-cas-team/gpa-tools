"""The jev category builder: TypeSafe's Jev model decides course membership.

Reads the per-student ``*.common.json`` in <work-folder>/transcripts/, asks Jev for each
distinct course how likely it is to belong to the category (scores are
cached), and writes the builder CSVs into <work-folder>/jev/.  A course is in
the category when its score is above the threshold.  Settings come from
config.yaml.  ``mode`` says how Jev is reached:

    cloud   through the gpa-tools cloud server (jev_cloud/), which holds the
            TypeSafe key and shares course scores between clients; this
            machine needs only a client token, in the variable cloud-token-env
    direct  TypeSafe itself (development only); the API key is in the
            variable api-key-env; model and api-url apply only here

    category-builder:
      jev:
        mode: direct
        cloud-url: https://gpa-tools-jev.<account>.workers.dev
        cloud-token-env: GPA_TOOLS_CLOUD_TOKEN
        model: jev-1.14.0
        api-url: https://api.typesafe.ai/v1/systemone
        api-key-env: GPA_TOOLS_KEY
        threshold: 0.5
        batch-size: 20

The keys themselves are never in config.yaml.
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

from .client import CloudClient, JevClient, JevError
from .store import ScoreCache

NAME = "jev"
log = logging.getLogger("jev_category")
MODES = ("direct", "cloud")
DEFAULTS = {
    "mode": "direct",
    "cloud-url": "",
    "cloud-token-env": "GPA_TOOLS_CLOUD_TOKEN",
    "model": "jev-1.14.0",
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


def jev_settings(raw: dict) -> dict:
    """config.yaml's ``category-builder: jev:`` section as the builder uses it."""
    jev_cfg = {**DEFAULTS, **category_builder.settings(raw, NAME)}
    return {
        "mode": str(jev_cfg["mode"] or "direct").strip().lower(),
        "cloud_url": str(jev_cfg["cloud-url"] or "").strip(),
        "token_env": jev_cfg["cloud-token-env"],
        "model": jev_cfg["model"],
        "url": jev_cfg["api-url"],
        "key_env": jev_cfg["api-key-env"],
        "threshold": float(jev_cfg["threshold"]),
        "batch_size": max(1, int(jev_cfg["batch-size"])),
    }


def load_config(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "in": category_builder.transcripts_folder(raw),
        "folder": category_builder.folder(raw, NAME),
        "log": Path(raw.get("log-folder") or "log").expanduser(),
        **jev_settings(raw),
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


def _cache(cfg: dict, category: str, definition: str, model: str | None = None) -> ScoreCache:
    """The local score cache, stamped with the model that answers (in cloud mode
    the server's, from client.model; else config.yaml's)."""
    return ScoreCache(cfg["folder"] / f"cache_{category.lower()}.json", definition,
                      model or cfg["model"])


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


def _model_or_none(cfg: dict) -> str | None:
    """In cloud mode the server's model (None when it cannot be asked); else None,
    which means config.yaml's."""
    if cfg["mode"] != "cloud":
        return None
    try:
        return client(cfg).model
    except (RunError, JevError, OSError, KeyError):
        return None


def estimate(cfg: dict, category: str, definition: str) -> dict:
    """How much Jev work a run of ``category`` would need right now."""
    students = load_students(cfg["in"])
    cache = _cache(cfg, category, definition, _model_or_none(cfg))
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


def cloud_token(cfg: dict) -> str:
    """cloud-token-env when set, else ~/.gpa-tools/client_token, picked up from the server
    once the owner approves this laptop's request (enroll.py); RunError with the notice else."""
    from . import enroll

    try:
        return enroll.token(cfg["token_env"])
    except enroll.NoToken as exc:
        raise RunError(str(exc)) from None


def client(cfg: dict):
    """The Jev client ``mode`` asks for (see the module doc); RunError when its
    key or token is not set or the mode is unknown."""
    if cfg["mode"] == "cloud":
        if not cfg["cloud_url"]:
            raise RunError("category-builder: jev: mode is cloud but cloud-url is not set in config.yaml")
        return CloudClient(cfg["cloud_url"], cloud_token(cfg))
    if cfg["mode"] != "direct":
        raise RunError(f"category-builder: jev: mode must be one of {', '.join(MODES)}, "
                       f"not {cfg['mode']!r}")
    return JevClient(cfg["url"], cfg["model"], api_key(cfg))


def _where(cfg: dict) -> str:
    return f"the gpa-tools cloud at {cfg['cloud_url']}" if cfg["mode"] == "cloud" \
        else f"TypeSafe at {cfg['url']}"


def run(cfg: dict, category: str, limit: int | None = None, progress=None) -> Path:
    """Score every student's courses for ``category`` and write the CSVs."""
    log.info("jev builder run: category %s, mode %s, threshold %s", category, cfg["mode"],
             cfg["threshold"])
    students = load_students(cfg["in"], limit)
    log.info("Loaded %d student(s) from %s", len(students), cfg["in"])

    jev = client(cfg)
    try:
        definition, how = definer.definition_for(cfg["config"], cfg["raw"], category)
    except OSError as exc:  # drafting a missing definition calls category-definer's model
        raise RunError(f"No definition for {category} and category-definer "
                       f"({definer.describe(definer.load_config(cfg['raw']))}) failed: {exc}") from None
    if note := definer.describe_how(category, how, definition):
        log.info("%s", note)
    try:
        model = jev.model  # in cloud mode the server says which model answers
        log.info("Jev model %s via %s", model, _where(cfg))
        cache = _cache(cfg, category, definition, model)
        if cache.reset:
            log.info("Definition or model changed since the last run; cached scores discarded.")
        classify(students, category, definition, cache, jev, cfg["threshold"],
                 cfg["batch_size"], progress)
    except JevError as exc:
        hint = ""
        if cfg["mode"] == "cloud" and str(exc).startswith("HTTP 401"):
            hint = (" -- this laptop's token was revoked or is unknown: contact the package owner "
                    "(a new one: python -m jev_category.enroll request --new --email <you>)")
        raise RunError(f"{_where(cfg)}: {exc}{hint}") from None
    except OSError as exc:  # URLError and socket timeouts are OSErrors
        raise RunError(f"Cannot reach {_where(cfg)}: {exc}") from None

    gpa_csv, courses_csv = write_outputs(students, category, cfg["folder"])
    log.info("Wrote %s", gpa_csv)
    log.info("Wrote %s", courses_csv)
    return gpa_csv
