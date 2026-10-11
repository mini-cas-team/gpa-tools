"""python -m category_builder --category STEM [--builder llm] [--publish]

Runs one category builder (default: config.yaml's active-category-builder)
into <work-folder>/<builder>/; --publish then copies <cat>_gpa.csv to
out-folder (same as python -m out_builder).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from . import active, get, names
from .common import RunError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m category_builder", description=__doc__)
    ap.add_argument("--category", required=True, help="category name, e.g. STEM")
    ap.add_argument("--builder", choices=names(), help="default: active-category-builder")
    ap.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    ap.add_argument("--limit", type=int, help="only process the first N students (for testing)")
    ap.add_argument("--publish", action="store_true", help="copy the result to out-folder")
    args = ap.parse_args(argv)

    raw = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    name = args.builder or active(raw)
    builder = get(name)
    cfg = builder.load_config(args.config)
    if hasattr(builder, "setup_logging"):
        builder.setup_logging(cfg["log"])
    try:
        builder.run(cfg, args.category.upper(), limit=args.limit)
    except RunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.publish:
        from out_builder import publish

        try:
            print(f"published {publish(raw, name, args.category)}")
        except (OSError, KeyError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
