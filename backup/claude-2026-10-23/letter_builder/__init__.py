"""letter-builder: recommendation letters, from PDF to an excellent/good/normal rank.

pdf2txt turns each <name>.pdf in config.yaml's letters-folder into <name>.txt
beside it; rank_letters asks TypeSafe's Jev to rank each letter and writes
<letters-folder>/letter_ranks.csv.  browser_view's Recommendations pages run
both on uploaded letters.

Run:  python -m letter_builder.pdf2txt
      python -m letter_builder.rank_letters
"""

from __future__ import annotations

from pathlib import Path

import yaml

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
