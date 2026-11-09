"""Load every *.json in <work-folder>/personal/ into an in-memory dict.

Upload → Profiles makes them from the <name>.profile files in
<input-folder>/personal/ (folders.py).

    PROFILES["adam_flores.json"] -> {"name": "Adam Flores", "age": 25, ...}

load() replaces PROFILES with what the folder holds now; a file that cannot be
read, or whose JSON is not an object, is left out and listed in ERRORS.

Run:  python -m profile.profile_load [--config config.yaml]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import yaml

import folders

log = logging.getLogger(__name__)

PROFILES: dict[str, dict] = {}  # file name -> its JSON object
ERRORS: dict[str, str] = {}     # file name -> why it was left out


def personal_folder(raw: dict) -> Path:
    """The <name>.profile files: <input-folder>/personal/ (KeyError without input-folder)."""
    return folders.input_folder(raw, "personal")


def personal_work_folder(raw: dict) -> Path:
    """The <name>.json made from them: <work-folder>/personal/ (KeyError without work-folder)."""
    return folders.work_folder(raw, "personal")


def load(folder: Path) -> dict[str, dict]:
    """Read every *.json in folder into PROFILES (replacing it) and return it."""
    profiles, errors = {}, {}
    for path in sorted(Path(folder).glob("*.json")):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors[path.name] = str(exc)
            continue
        if not isinstance(data, dict):
            errors[path.name] = f"expected a JSON object, got {type(data).__name__}"
            continue
        profiles[path.name] = data
    for name, error in errors.items():
        log.warning("profile %s skipped: %s", name, error)
    PROFILES.clear()
    PROFILES.update(profiles)
    ERRORS.clear()
    ERRORS.update(errors)
    return PROFILES


def load_cfg(raw: dict) -> dict[str, dict]:
    """load() <work-folder>/personal/; no profiles when work-folder is not set."""
    try:
        folder = personal_work_folder(raw)
    except KeyError:
        PROFILES.clear()
        ERRORS.clear()
        return PROFILES
    return load(folder)


def load_config(config_path: Path) -> dict[str, dict]:
    """load() the <work-folder>/personal/ named in config_path."""
    try:
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    return load_cfg(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = ap.parse_args(argv)
    profiles = load_config(args.config)
    print(f"{len(profiles)} profile(s) loaded")
    for name, error in ERRORS.items():
        print(f"  skipped {name}: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
