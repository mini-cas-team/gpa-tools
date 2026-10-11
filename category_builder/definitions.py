"""Category definitions: config.yaml's ``category-definitions`` section.

Shared by every builder.  A definition is the user's: the definer only drafts
a starting point, and it is edited here or in the Upload tab before a run.
"""

from __future__ import annotations

from pathlib import Path

import yaml


SECTION = "category-definitions"
SECTION_HEADER = (
    "# --- category definitions (all builders) ------------------------------------\n"
    "# Shared by every category builder; drafted by category-definer, edit freely.\n"
    "# Changing a definition clears the llm builder's cached answers for it.\n"
    "# Delete an entry to have category-definer draft a new one.\n"
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
