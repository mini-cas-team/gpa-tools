"""out-builder: publish a builder's result to out-folder.

Copies <work-folder>/<builder>/<cat>_gpa.csv to <out-folder>/<cat>_gpa.csv,
the public output other applications read.  The file name carries no builder:
whichever builder was published last for a category is what out-folder holds.

The browser_view Upload tab publishes after each run it starts; a run from
the command line publishes only with --publish, or with:

    python -m out_builder --category STEM [--builder llm]
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import category_builder
from category_builder.common import gpa_csv_name


def out_folder(cfg: dict) -> Path:
    if not cfg.get("out-folder"):
        raise KeyError("out-folder is not set in config.yaml")
    return Path(cfg["out-folder"]).expanduser()


def publish(cfg: dict, builder: str, category: str) -> Path:
    """Copy one builder's <cat>_gpa.csv into out-folder; returns the copy."""
    category_builder.get(builder)  # an unknown name fails here, not as a missing file
    source = category_builder.folder(cfg, builder) / gpa_csv_name(category)
    if not source.is_file():
        raise FileNotFoundError(f"no {source.name} from the {builder} builder in {source.parent}")
    out = out_folder(cfg)
    out.mkdir(parents=True, exist_ok=True)
    target = out / source.name
    # Copy beside it, then rename: a reader never sees half a file.
    tmp = target.with_name(target.name + ".tmp")
    shutil.copyfile(source, tmp)
    os.replace(tmp, target)
    return target
