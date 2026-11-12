"""Category builders: the registry, and what every builder shares.

A category builder reads the per-student ``*.common.json`` files in
<work-folder>/transcripts/, decides which courses belong to a category, and writes two CSVs
into its own folder, ``<work-folder>/<name>/`` (see ``common.write_outputs``):

    <cat>_gpa.csv       one row per student: category GPA and course counts
    <cat>_courses.csv   one row per course, with its in-category flag

It may keep any working files of its own there too (jev keeps its score
cache, category_builder.cache).  Builders provide data only: ranking is done by whoever reads the
CSV (the View tab sorts its columns).  out_builder publishes ``<cat>_gpa.csv``
to out-folder.

A builder is a module with:

    load_config(config_path) -> dict      must include "folder" (its work folder)
    run(cfg, category, progress=None) -> Path      the <cat>_gpa.csv written
    estimate(cfg, category, definition) -> dict    optional: {"seconds", ...}

Adding one is a module with those functions plus a line in BUILDERS, and a
``category-builder: <name>:`` section in config.yaml for its settings.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import folders

# name -> module implementing the builder functions above
BUILDERS = {
    "jev": "jev_category.builder",
}
DEFAULT_BUILDER = "jev"


class UnknownBuilder(ValueError):
    pass


def names() -> list[str]:
    return list(BUILDERS)


def get(name: str) -> ModuleType:
    if name not in BUILDERS:
        raise UnknownBuilder(f"unknown category builder {name!r} (known: {', '.join(BUILDERS)})")
    return importlib.import_module(BUILDERS[name])


def active(cfg: dict) -> str:
    """config.yaml's active-category-builder, else jev (also for an older config's llm)."""
    name = cfg.get("active-category-builder") or DEFAULT_BUILDER
    return name if name in BUILDERS else DEFAULT_BUILDER


RUN_MODES = ("prod", "dev")


def run_mode(cfg: dict) -> str:
    """config.yaml's run_mode: "dev" only when it says dev; anything else is "prod".

    prod  a production laptop: category definitions come only from the cloud server
          (no drafting, no editing), and developer details stay hidden in the UI
    dev   a developer's machine: everything, including drafting new categories
    """
    return "dev" if str(cfg.get("run_mode") or "").strip().lower() == "dev" else "prod"


def settings(cfg: dict, name: str) -> dict:
    """That builder's own section: config.yaml's category-builder: <name>:."""
    return dict((cfg.get("category-builder") or {}).get(name) or {})


def work_folder(cfg: dict) -> Path:
    """config.yaml's work-folder (KeyError when it is not set)."""
    return folders.work_root(cfg)


def transcripts_folder(cfg: dict) -> Path:
    """Where every builder reads the *.common.json: <work-folder>/transcripts/."""
    return folders.work_folder(cfg, "transcripts")


def folder(cfg: dict, name: str) -> Path:
    """Where builder ``name`` keeps its files: <work-folder>/<name>/."""
    return work_folder(cfg) / name
