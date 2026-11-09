"""letter-builder: recommendation letters, from PDF to an excellent/good/normal rank.

pdf2txt turns each <name>.pdf in <input-folder>/letters/ into <name>.txt in
<work-folder>/letters/; rank_letters asks TypeSafe's Jev to rank each letter
and writes <work-folder>/letters/letter_ranks.csv (folders.py).  browser_view's Recommendations pages run
both on uploaded letters.

Run:  python -m letter_builder.pdf2txt
      python -m letter_builder.rank_letters
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

import folders
from upload.convert import add_tool_folders

RANKS_CSV = "letter_ranks.csv"


def load_raw(config_path: Path) -> dict:
    try:
        return yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def letters_folder(raw: dict) -> Path:
    """The letter PDFs: <input-folder>/letters/ (KeyError without input-folder)."""
    return folders.input_folder(raw, "letters")


def letters_work_folder(raw: dict) -> Path:
    """Their .txt and letter_ranks.csv: <work-folder>/letters/ (KeyError without work-folder)."""
    return folders.work_folder(raw, "letters")


def parse_cli(parser: argparse.ArgumentParser, argv=None):
    """Add --folder, --work and --config, parse, and return
    (args, config, letter PDF folder, work folder)."""
    parser.add_argument("--folder", type=Path, help="letter PDFs; default <input-folder>/letters")
    parser.add_argument("--work", type=Path, help="their .txt and ranks; default <work-folder>/letters")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args(argv)
    raw = load_raw(args.config)
    add_tool_folders(raw.get("tool-folders"))
    try:
        folder = args.folder.expanduser() if args.folder else letters_folder(raw)
        work = args.work.expanduser() if args.work else letters_work_folder(raw)
    except KeyError as exc:
        raise SystemExit(str(exc).strip("'\""))
    return args, raw, folder, work
