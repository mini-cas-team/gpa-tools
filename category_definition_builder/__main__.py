"""Draft a category definition and print it with every attempt; nothing is saved.

    python -m category_definition_builder STEM [--config config.yaml] [--mode prod] [--only gemini]

Uses config.yaml's category-definer settings (mode, retries, browser), like
the Upload tab's Add.  --mode overrides the mode; --only keeps just that step
of the mode, e.g. to check a site's selectors still work.  Exit status is 0
when a step gave a definition.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    from category_builder import definer

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("category", help="the category key, e.g. STEM")
    ap.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    ap.add_argument("--mode", choices=list(definer.MODES), help="instead of config.yaml's mode")
    ap.add_argument("--only", choices=list(definer.PROVIDERS), help="try only this step")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) if args.config.exists() else {}
    cfg = cfg or {}
    if args.mode:
        cfg["category-definer"] = {**(cfg.get("category-definer") or {}), "mode": args.mode}
    category = args.category.strip().upper()
    try:
        draft = definer.draft(cfg, category, only=args.only)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except definer.DraftFailed as exc:
        print(json.dumps({"category": category, "definition": None,
                          "attempts": [a.to_dict() for a in exc.attempts]}, indent=2))
        return 1
    print(json.dumps({"category": category, "definition": draft.definition, "source": draft.source,
                      "attempts": [a.to_dict() for a in draft.attempts]}, indent=2,
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
