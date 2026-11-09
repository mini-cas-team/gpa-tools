"""Fill in what a profile leaves out, from the student's transcript.

    fields = transcript_fields(work_folder)   # {student.name: {"institution": ..., "program": ...}}
    add_missing(profile, fields)              # -> the attributes it added

Each attribute in FIELDS that a profile lacks is added with the value from the
transcript whose student.name equals the profile's "name" (matched exactly),
or None when no transcript has that name. An attribute the profile already
has, even null, is kept.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


# profile attribute -> how to read it from a transcript's common JSON
FIELDS = {
    "institution": lambda t: _text((t.get("institution") or {}).get("name")
                                   if isinstance(t.get("institution"), dict) else t.get("institution")),
    # as category_builder calls it: the student's major
    "program": lambda t: _text((t.get("student") or {}).get("major")),
}


def transcript_fields(work_folder: Path | None) -> dict[str, dict[str, str | None]]:
    """{student.name: {attribute: value}} for every <work_folder>/*.common.json;
    a name on two transcripts keeps the first (by file name)."""
    if work_folder is None:
        return {}
    found: dict[str, dict[str, str | None]] = {}
    for path in sorted(Path(work_folder).expanduser().glob("*.common.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            log.warning("enrich: %s skipped: %s", path.name, exc)
            continue
        if not isinstance(data, dict):
            continue
        name = _text((data.get("student") or {}).get("name"))
        if name:
            found.setdefault(name, {key: read(data) for key, read in FIELDS.items()})
    return found


def add_missing(profile: dict, fields: dict[str, dict[str, str | None]]) -> list[str]:
    """Add each FIELDS attribute ``profile`` lacks; return the ones added."""
    name = profile.get("name")
    transcript = fields.get(name.strip(), {}) if isinstance(name, str) else {}
    added = [key for key in FIELDS if key not in profile]
    for key in added:
        profile[key] = transcript.get(key)
    return added


def has_transcript(profile: dict, fields: dict) -> bool:
    name = profile.get("name")
    return isinstance(name, str) and name.strip() in fields
