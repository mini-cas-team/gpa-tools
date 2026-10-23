"""letter-builder: recommendation letters, from PDF to an excellent/good/normal rank.

pdf2txt turns each <name>.pdf in config.yaml's letters-folder into <name>.txt
beside it; rank_letters asks TypeSafe's Jev to rank each letter and writes
<letters-folder>/letter_ranks.csv.  browser_view's Recommendations pages run
both on uploaded letters.

Run:  python -m letter_builder.pdf2txt
      python -m letter_builder.rank_letters
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from upload.convert import add_tool_folders

DEFAULT_FOLDER = "~/Templates/letters"
RANKS_CSV = "letter_ranks.csv"


def load_raw(config_path: Path) -> dict:
    try:
        return yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def letters_folder(raw: dict) -> Path:
    """config.yaml's letters-folder, else ~/Templates/letters."""
    return Path(raw.get("letters-folder") or DEFAULT_FOLDER).expanduser()


def parse_cli(parser: argparse.ArgumentParser, argv=None):
    """Add --folder and --config, parse, and return (args, config, letters folder)."""
    parser.add_argument("--folder", type=Path, help="default: config.yaml's letters-folder")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args(argv)
    raw = load_raw(args.config)
    add_tool_folders(raw.get("tool-folders"))
    return args, raw, (args.folder.expanduser() if args.folder else letters_folder(raw))
