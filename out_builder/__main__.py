"""python -m out_builder --category STEM [--builder llm] [--config config.yaml]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

import category_builder

from . import publish

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m out_builder",
                                 description="Copy a builder's <cat>_gpa.csv to out-folder.")
    ap.add_argument("--category", required=True, help="category name, e.g. STEM")
    ap.add_argument("--builder", choices=category_builder.names(),
                    help="default: active-category-builder")
    ap.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    try:
        target = publish(cfg, args.builder or category_builder.active(cfg), args.category)
    except (OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"published {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
