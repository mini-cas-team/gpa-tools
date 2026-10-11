"""Category builders: the registry, and what every builder shares.

A category builder reads the per-student ``*.common.json`` files in
work-folder, decides which courses belong to a category, and writes two CSVs
into its own folder, ``<work-folder>/<name>/`` (see ``common.write_outputs``):

    <cat>_gpa.csv       one row per student: category GPA and course counts
    <cat>_courses.csv   one row per course, with its in-category flag

It may keep any working files of its own there too (llm keeps its answer
cache).  Builders provide data only: ranking is done by whoever reads the
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

# name -> module implementing the builder functions above
BUILDERS = {
    "llm": "llm_category.builder",
}
DEFAULT_BUILDER = "llm"


class UnknownBuilder(ValueError):
    pass


def names() -> list[str]:
    return list(BUILDERS)


def get(name: str) -> ModuleType:
    if name not in BUILDERS:
        raise UnknownBuilder(f"unknown category builder {name!r} (known: {', '.join(BUILDERS)})")
    return importlib.import_module(BUILDERS[name])


def active(cfg: dict) -> str:
    """config.yaml's active-category-builder, else llm."""
    name = cfg.get("active-category-builder") or DEFAULT_BUILDER
    return name if name in BUILDERS else DEFAULT_BUILDER


def settings(cfg: dict, name: str) -> dict:
    """That builder's own section: config.yaml's category-builder: <name>:."""
    return dict((cfg.get("category-builder") or {}).get(name) or {})


def work_folder(cfg: dict) -> Path:
    if not cfg.get("work-folder"):
        raise KeyError("work-folder is not set in config.yaml")
    return Path(cfg["work-folder"]).expanduser()


def folder(cfg: dict, name: str) -> Path:
    """Where builder ``name`` keeps its files: <work-folder>/<name>/."""
    return work_folder(cfg) / name
