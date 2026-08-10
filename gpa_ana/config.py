"""config.yaml loading.

The two required keys use the hyphenated spelling from the project brief::

    pdf-folder: /path/to/transcripts
    category: STEM
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path("config.yaml")


def _categories(value, path: Path) -> list[str]:
    """``category`` is a list; a bare string is accepted as a list of one.

    Every listed category is ranked in the same run, off one parse of each PDF.
    Order is preserved and duplicates are dropped, so appending a name is
    always safe.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ConfigError(f"{path}: category must be a name or a list of names")

    names: list[str] = []
    for item in value:
        name = str(item).strip()
        if name and name not in names:
            names.append(name)
    if not names:
        raise ConfigError(f"{path}: category list is empty")
    return names


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    pdf_folder: Path
    categories: list[str]
    out_folder: Path = Path("out")
    min_category_courses: int = 0
    force_ocr: bool = False
    ocr_dpi: int = 300
    jobs: int = 0  # 0 = one per available CPU

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = Path(path or DEFAULT_CONFIG)
        if not path.exists():
            raise ConfigError(f"config file not found: {path}")

        raw = yaml.safe_load(path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: expected a mapping at the top level")

        missing = [k for k in ("pdf-folder", "category") if k not in raw]
        if missing:
            raise ConfigError(f"{path}: missing required key(s): {', '.join(missing)}")

        folder = Path(str(raw["pdf-folder"])).expanduser()
        if not folder.is_dir():
            raise ConfigError(f"{path}: pdf-folder is not a directory: {folder}")

        if "min-category-credits" in raw:
            raise ConfigError(
                f"{path}: min-category-credits was replaced by min-category-courses. "
                f"Credit units are not comparable across schools (1 per course at Penn, "
                f"100 at Chicago, 3-4 elsewhere), so the threshold counts courses now -- "
                f"rename the key and set a course count"
            )

        if "rank-by" in raw:
            raise ConfigError(
                f"{path}: rank-by is no longer configurable. Every student is ranked "
                f"by capped GPA (rescaled so A = 4.00 everywhere, then capped), the "
                f"only rule that compares fairly across schools -- remove the key"
            )

        return cls(
            pdf_folder=folder,
            categories=_categories(raw["category"], path),
            out_folder=Path(str(raw.get("out-folder", "out"))).expanduser(),
            min_category_courses=int(raw.get("min-category-courses", 0) or 0),
            force_ocr=bool(raw.get("force-ocr", False)),
            ocr_dpi=int(raw.get("ocr-dpi", 300)),
            jobs=int(raw.get("jobs", 0) or 0),
        )

    @property
    def worker_count(self) -> int:
        return self.jobs if self.jobs > 0 else (os.cpu_count() or 1)

    def pdfs(self) -> list[Path]:
        found = sorted(p for p in self.pdf_folder.glob("*.pdf") if p.is_file())
        if not found:
            raise ConfigError(f"no PDFs found in {self.pdf_folder}")
        return found
