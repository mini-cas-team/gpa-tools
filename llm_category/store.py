"""Category definitions (in config.yaml) and the per-course answer cache."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


SECTION = "category-definitions"
SECTION_HEADER = (
    "# --- category definitions (llm_category) ------------------------------------\n"
    "# Written by llm_category and the browser_view Upload tab; edit freely.\n"
    "# Changing a definition clears that category's cached answers on the next run.\n"
    "# Delete an entry to have the LLM write a new one.\n"
)


class Definitions:
    """The ``category-definitions:`` section of config.yaml: name -> definition.

    Only that section is ever rewritten -- the rest of config.yaml, comments
    included, is left byte for byte -- so the file stays hand-editable.
    """

    def __init__(self, config_path: Path):
        self.path = config_path
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else None
        self.data: dict[str, str] = dict((cfg or {}).get(SECTION) or {})

    def get_or_create(self, category: str, client) -> tuple[str, bool]:
        if category in self.data:
            return self.data[category].strip(), False
        definition = client.define(category)
        self.set(category, definition)
        return definition, True

    def set(self, category: str, definition: str) -> None:
        self.data[category] = definition.strip()
        self._write()

    def _write(self) -> None:
        text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        lines = text.splitlines(keepends=True)
        block = SECTION_HEADER + yaml.safe_dump(
            {SECTION: dict(sorted(self.data.items()))}, allow_unicode=True, width=88
        )

        start = next((i for i, line in enumerate(lines) if line.startswith(f"{SECTION}:")), None)
        if start is None:
            before, after = text.rstrip("\n") + "\n\n" if text.strip() else "", ""
        else:
            # The section runs until the next top-level key; take our own
            # header comment above it along with it.
            end = next((i for i in range(start + 1, len(lines))
                        if lines[i].strip() and not lines[i][0].isspace()
                        and not lines[i].startswith("#")), len(lines))
            header = SECTION_HEADER.splitlines(keepends=True)
            if lines[max(0, start - len(header)):start] == header:
                start -= len(header)
            before = "".join(lines[:start])
            tail = "".join(lines[end:])
            after = "\n" + tail if tail else ""
        new_text = before + block + after

        # Never leave config.yaml unreadable or with different values.
        reread = yaml.safe_load(new_text) or {}
        if reread.get(SECTION) != self.data:
            raise ValueError(f"could not update {SECTION} in {self.path} safely")
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(self.path)


class AnswerCache:
    """Cached yes/no answers for one category, keyed by "code|title".

    Tied to the definition text and model: if either changes, old answers are
    discarded because they were given to a different question.
    """

    def __init__(self, path: Path, definition: str, model: str):
        self.path = path
        self.stamp = hashlib.sha256(f"{model}\n{definition}".encode()).hexdigest()[:16]
        self.answers: dict[str, bool] = {}
        # Seconds spent per LLM answer, for the Upload tab's time estimate.
        self.timing = {"count": 0, "seconds": 0.0}
        self.reset = False
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("stamp") == self.stamp:
                self.answers = saved["answers"]
                self.timing = saved.get("timing") or self.timing
            else:
                self.reset = True

    @staticmethod
    def key(code: str, title: str) -> str:
        return f"{code.strip().upper()}|{title.strip().lower()}"

    def get(self, code: str, title: str) -> bool | None:
        return self.answers.get(self.key(code, title))

    def put(self, code: str, title: str, answer: bool, seconds: float | None = None) -> None:
        self.answers[self.key(code, title)] = answer
        if seconds is not None:
            self.timing["count"] += 1
            self.timing["seconds"] += seconds
        self.save()  # every answer is ~10 s of LLM time; don't lose it on Ctrl-C

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"stamp": self.stamp, "answers": self.answers, "timing": self.timing},
                       indent=1, sort_keys=True), encoding="utf-8"
        )
