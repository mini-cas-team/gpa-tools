"""Category axis: decide which courses belong to a taxonomy.

Rulesets read ``code`` / ``dept`` / ``title`` off the canonical record and know
nothing about columns, regions or OCR.  Adding a taxonomy (medical, humanities)
is a new YAML file here and no change to the layout axis.

Membership is a **set**, not a single value: BIOCHEM 110 is STEM *and* medical,
so a course can carry several categories at once.

Department maps are keyed by institution because the same token means different
things at different schools -- ``PS`` is Political Science at Caltech, ``ENG``
is English at Princeton.  A department seen in neither the include nor the
exclude list is reported as unmapped rather than silently treated as excluded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

RULESET_DIR = Path(__file__).parent / "rulesets"


class RulesetError(RuntimeError):
    pass


@dataclass
class Decision:
    in_category: bool
    reason: str
    unmapped: bool = False
    disputed: bool = False  # dept map and title keywords disagree


@dataclass
class Ruleset:
    id: str
    description: str = ""
    institutions: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    generic_include: list[str] = field(default_factory=list)
    title_audit: dict[str, list[str]] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        self._include: dict[str, set[str]] = {}
        self._exclude: dict[str, set[str]] = {}
        for inst, lists in self.institutions.items():
            self._include[inst] = {_norm(d) for d in (lists or {}).get("include", [])}
            self._exclude[inst] = {_norm(d) for d in (lists or {}).get("exclude", [])}
        self._generic = {_norm(d) for d in self.generic_include}
        self._kw_in = [k.lower() for k in self.title_audit.get("include_keywords", [])]
        self._kw_out = [k.lower() for k in self.title_audit.get("exclude_keywords", [])]

    # ------------------------------------------------------------------
    def classify(self, course, institution_id: str | None) -> Decision:
        dept = _norm(course.dept) if course.dept else None
        include = self._include.get(institution_id or "", set())
        exclude = self._exclude.get(institution_id or "", set())
        known_institution = bool(include or exclude)

        title_hit = self._title_says_in(course.title)

        if dept and dept in include:
            return Decision(True, f"dept {course.dept}", disputed=title_hit is False)
        if dept and dept in exclude:
            return Decision(False, f"dept {course.dept}", disputed=title_hit is True)

        if not known_institution:
            if dept and dept in self._generic:
                return Decision(True, f"generic dept {course.dept}")
            if title_hit:
                return Decision(True, "title keyword", unmapped=True)
            return Decision(False, "no generic dept match", unmapped=True)

        # Institution is known but this department is not in either list --
        # a new department has appeared and the ruleset needs extending.
        return Decision(
            bool(title_hit),
            f"unmapped dept {course.dept}",
            unmapped=True,
        )

    def _title_says_in(self, title: str) -> bool | None:
        low = title.lower()
        if any(re.search(rf"\b{re.escape(k)}", low) for k in self._kw_out):
            return False
        if any(re.search(rf"\b{re.escape(k)}", low) for k in self._kw_in):
            return True
        return None


def _norm(token: str | None) -> str:
    return re.sub(r"\s+", " ", (token or "")).strip().upper()


def load_ruleset(name: str, directory: Path | None = None) -> Ruleset:
    directory = directory or RULESET_DIR
    path = directory / f"{name.lower()}.yaml"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in directory.glob("*.yaml"))) or "none"
        raise RulesetError(f"no ruleset for category {name!r} (available: {available})")
    raw = yaml.safe_load(path.read_text()) or {}
    raw.setdefault("id", name)
    return Ruleset(**raw)
