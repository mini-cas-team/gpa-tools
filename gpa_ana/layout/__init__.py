"""Layout axis: profile registry and dispatch.

A *profile* is per institution+format and is usually pure YAML.  It picks a
*handler* (a reusable layout algorithm) and parameterises it.  Adding a school
whose shape is already covered means adding a YAML file and no Python at all;
a new handler is only needed when the page *shape* is genuinely novel.

Dispatch fingerprints every profile against the page text, scores the matches
and takes the best.  No confident match -> the file is reported as
``needs_review`` rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .handlers import term_block_table

PROFILE_DIR = Path(__file__).parent / "profiles"

# Words that stay lowercase inside an institution name, unless they lead it.
_JOINERS = {"of", "the", "and", "at", "for", "in", "on"}


def _titlecase_institution(name: str) -> str:
    """"PRINCE GEORGE'S COMMUNITY COLLEGE" -> "Prince George's Community College".

    ``str.title`` is unusable here: it capitalises after an apostrophe
    ("John'S") and mangles the parenthesised acronyms registrars append.
    """

    def one(word: str) -> str:
        if word.startswith("(") and word.endswith(")"):
            return word  # an acronym the registrar chose, e.g. (MICA)
        # Capitalise at the start and after a hyphen or period -- never after an
        # apostrophe, which is what breaks str.title.
        fixed = re.sub(
            r"(^|[-.])([a-z])",
            lambda m: m.group(1) + m.group(2).upper(),
            word.lower(),
        )
        # Mc/Mac keep an internal capital: mcdaniel -> McDaniel.
        return re.sub(r"^(Mc)([a-z])", lambda m: m.group(1) + m.group(2).upper(), fixed)

    words = name.split()
    out = []
    for position, word in enumerate(words):
        fixed = one(word)
        if position and fixed.lower().rstrip(",") in _JOINERS:
            fixed = fixed.lower()
        out.append(fixed)
    return " ".join(out)

HANDLERS = {
    "term_block_table": term_block_table,
}


class ProfileError(RuntimeError):
    pass


@dataclass
class Profile:
    id: str
    institution: str
    institution_from: str | None = None
    handler: str = "term_block_table"
    fingerprint: dict[str, Any] = field(default_factory=dict)
    code_grammar: str = "space_suffix"
    grade_scale: dict[str, float] | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    # -- institution name --------------------------------------------------
    def institution_for(self, lines: list[str]) -> str:
        """Display name for one document.

        Fixed in ``institution`` for a single-school profile.  A profile shared
        by a family of schools that print the same layout sets
        ``institution_from: first_line`` instead and the name is read off the
        page, so adding a school to the family costs a fingerprint entry rather
        than a whole file.
        """
        if not self.institution_from:
            return self.institution
        if self.institution_from == "first_line":
            for line in lines:
                if line.strip():
                    return _titlecase_institution(line.strip())
            return self.institution
        raise ProfileError(
            f"{self.id}: unknown institution_from {self.institution_from!r}"
        )

    # -- dept extraction ---------------------------------------------------
    def dept_of(self, code: str | None) -> str | None:
        """Course code -> department token, per this institution's grammar."""
        if not code:
            return None
        code = code.strip()
        grammar = self.code_grammar

        if grammar == "mit_dotted":
            # 6.036 -> 6 ; 21W.011 -> 21W ; 8.THU -> 8
            return code.split(".")[0] or None

        if grammar == "jhu_dotted":
            # AS.270.103 -> 270 (school prefix carries no subject meaning)
            parts = code.split(".")
            return parts[1] if len(parts) >= 3 else (parts[0] or None)

        if grammar == "alpha_prefix":
            # Department and number are fused: SM221 -> SM ; EE301 -> EE.
            match = re.match(r"[A-Za-z]+", code)
            return match.group(0) if match else None

        if grammar == "space_suffix":
            # Everything before the final token: "CIV ENG 100" -> "CIV ENG",
            # "COMS W1004" -> "COMS", "MS&E 111" -> "MS&E".
            parts = code.split()
            return " ".join(parts[:-1]) if len(parts) > 1 else code

        raise ProfileError(f"{self.id}: unknown code_grammar {grammar!r}")

    @property
    def module(self):
        try:
            return HANDLERS[self.handler]
        except KeyError:
            raise ProfileError(f"{self.id}: unknown handler {self.handler!r}") from None

    # -- dispatch ----------------------------------------------------------
    def score(self, text: str) -> int:
        """How strongly this profile claims the document. 0 = no claim."""
        haystack = text.upper()
        total = 0
        for needle in self.fingerprint.get("text_any", []):
            if needle.upper() in haystack:
                total += 10
        for needle in self.fingerprint.get("text_all", []):
            if needle.upper() not in haystack:
                return 0
            total += 5
        for pattern in self.fingerprint.get("regex_any", []):
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                total += 8
        return total


def load_profiles(directory: Path | None = None) -> list[Profile]:
    directory = directory or PROFILE_DIR
    profiles: list[Profile] = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text()) or {}
        if raw.pop("abstract", False):
            continue
        try:
            profiles.append(Profile(**raw))
        except TypeError as exc:
            raise ProfileError(f"{path.name}: {exc}") from None
    if not profiles:
        raise ProfileError(f"no layout profiles found in {directory}")
    return profiles


GENERIC_PROFILE = Profile(
    id="generic_us_term_block",
    institution="(unrecognised institution)",
    notes="Fallback so an unknown school still parses; output is flagged for review.",
)


def dispatch(text: str, profiles: list[Profile]) -> tuple[Profile, int]:
    """Pick the best-matching profile for a document."""
    best, best_score = GENERIC_PROFILE, 0
    for profile in profiles:
        score = profile.score(text)
        if score > best_score:
            best, best_score = profile, score
    return best, best_score
