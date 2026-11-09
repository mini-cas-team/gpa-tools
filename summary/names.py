"""The union of student names across every upload type.

    transcripts  student.name of each <work-folder>/transcripts/*.common.json
    profiles     "name" of each <work-folder>/personal/*.json
    letters      <input-folder>/letters/<key>_letter<N>.pdf carry no name: the name of the
                 transcript or profile with the same key, else the key title-cased
                 (adam_flores -> Adam Flores)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import folders
from letter_builder.students import student_key
from profile import profile_load

log = logging.getLogger(__name__)


def _name(data: object) -> str:
    raw = data.get("name") if isinstance(data, dict) else None
    return raw.strip() if isinstance(raw, str) else ""


def transcript_names(cfg: dict) -> dict[str, str]:
    """{file key: student.name} for every uploaded transcript with a name."""
    try:
        work = folders.work_folder(cfg, "transcripts")
    except KeyError:
        return {}
    names = {}
    for path in sorted(work.glob("*.common.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            log.warning("names: %s skipped: %s", path.name, exc)
            continue
        name = _name(data.get("student") if isinstance(data, dict) else None)
        if name:
            names[path.name[:-len(".common.json")]] = name
    return names


def _profile_names(cfg: dict) -> dict[str, str]:
    """{file key: "name"} for every profile with a name."""
    profiles = profile_load.load_cfg(cfg)
    return {Path(f).stem: n for f, d in profiles.items() if (n := _name(d))}


def letter_names(cfg: dict, transcripts: dict[str, str] | None = None,
                 profiles: dict[str, str] | None = None) -> dict[str, str]:
    """{student key: name} for every student with a letter in <input-folder>/letters: the
    transcript's student.name, else the profile's name, else the key title-cased."""
    transcripts = transcript_names(cfg) if transcripts is None else transcripts
    profiles = _profile_names(cfg) if profiles is None else profiles
    inbox = folders.input_or_none(cfg, "letters")
    keys = {student_key(pdf) for pdf in inbox.glob("*.pdf")} if inbox else set()
    return {key: transcripts.get(key) or profiles.get(key) or key.replace("_", " ").title()
            for key in keys}


def name_keys(cfg: dict) -> dict[str, str]:
    """{student name: file key} for every name from transcripts, profiles and
    letters, sorted by name; a name on several keys keeps its transcript's, then
    its profile's, then the first letter key (by key)."""
    transcripts, profiles = transcript_names(cfg), _profile_names(cfg)
    keys: dict[str, str] = {}
    for source in (transcripts, profiles, letter_names(cfg, transcripts, profiles)):
        for key in sorted(source):
            keys.setdefault(source[key], key)
    return {name: keys[name] for name in sorted(keys, key=str.casefold)}


def upload_names(cfg: dict) -> list[str]:
    """Every student name from transcripts, profiles and letters, sorted, once each."""
    return list(name_keys(cfg))
