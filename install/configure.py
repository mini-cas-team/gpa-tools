"""Create or update config.yaml for this machine.

    .venv312/bin/python install/configure.py --data ~/gpa-tools-data \\
        --model qwen2.5:3b --ollama-url http://localhost:11434

--model and --ollama-url set both the llm category builder and the
category-definer; --definer-model then gives the definer its own model.

Without config.yaml, it is created from TEMPLATE below. Only the keys
given on the command line change; every other line, comments included, is
kept as it is. The result is re-read to check it is valid YAML with the
intended values before it replaces the old file.

Run it with the venv's Python (it needs PyYAML).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / "config.yaml"
FOLDERS = {"pdf-folder": "transcripts", "work-folder": "work", "out-folder": "out", "log-folder": "log"}
# A new config.yaml starts as this; main() then fills in what it is given.
TEMPLATE = """\
# gpa-tools configuration for this machine.
#
# Made by install/configure.py (or /install-gpa-tools in Claude Code); edit freely.

# --- folders ------------------------------------------------------------------
pdf-folder: ~/gpa-tools-data/transcripts # transcript PDFs to read
work-folder: ~/gpa-tools-data/work # .txt/.common.json; builders keep their files in <work-folder>/<builder>/
out-folder: ~/gpa-tools-data/out # published <cat>_gpa.csv, for other applications
log-folder: ~/gpa-tools-data/log # upload.log, llm_category.log, browser_view.log

# --- View tab -------------------------------------------------------------------
min-category-courses: 4 # hide students with fewer category courses

# --- external tools -------------------------------------------------------------
# Folders holding pdftotext when it is not on PATH
# (the installer puts them in .tools/ when there is no admin access).
# tool-folders: []

# --- category builders --------------------------------------------------------
# python -m category_builder --category STEM [--builder llm] [--publish]
# Each reads the *.common.json in work-folder and writes <cat>_gpa.csv and
# <cat>_courses.csv into <work-folder>/<builder>/. python -m out_builder copies
# <cat>_gpa.csv to out-folder (the Upload tab does that after each run).
active-category-builder: llm # the default in the Upload and View tabs
category-builder:
  llm: # a local LLM answers yes/no per course
    model: qwen2.5:3b # any model in `ollama list`; the installer picks one for this machine
    ollama-url: http://localhost:11434

# --- category-definer: drafts a definition for the user to edit -------------------
category-definer:
  provider: ollama
  model: qwen2.5:3b # a bigger model drafts better; it is one call per category
  ollama-url: http://localhost:11434

# --- category definitions (all builders) ------------------------------------
# Shared by every category builder; drafted by category-definer, edit freely.
# Changing a definition clears the llm builder's cached answers for it.
# Delete an entry to have category-definer draft a new one.
category-definitions: {}
"""


def scalar(value: str) -> str:
    """value as YAML: plain when that reads back unchanged, else double-quoted."""
    try:
        if yaml.safe_load(f"k: {value}") == {"k": value}:
            return value
    except yaml.YAMLError:
        pass
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def path_value(raw: str) -> str:
    """An absolute path with forward slashes (fine on Windows, no YAML escapes)."""
    return Path(raw).expanduser().resolve().as_posix()


def set_top(text: str, key: str, rendered: str) -> str:
    """Set a top-level key, uncommenting ``# key:`` if that is all there is.
    A trailing comment on the line is kept."""
    line = re.compile(rf"^(#\s*)?{re.escape(key)}:(?P<rest>.*)$", re.M)
    for match in line.finditer(text):
        if match.group(1) and re.search(rf"^{re.escape(key)}:", text, re.M):
            continue  # a live line exists further on; leave the comment alone
        comment = re.search(r"\s+#.*$", match.group("rest"))
        keep = comment.group(0) if comment and not match.group(1) else ""
        return text[:match.start()] + f"{key}: {rendered}{keep}" + text[match.end():]
    return text.rstrip("\n") + f"\n{key}: {rendered}\n"


def set_nested(text: str, parents: str | list[str], key: str, rendered: str) -> str:
    """Set ``parents[0]: parents[1]: ... key:`` (two-space indents), adding what is missing."""
    return _set_in(text, 0, [parents] if isinstance(parents, str) else list(parents), key, rendered)


def _set_in(text: str, depth: int, parents: list[str], key: str, rendered: str) -> str:
    pad = "  " * depth
    if not parents:
        line = re.compile(rf"^{pad}{re.escape(key)}:(?P<rest>.*)$", re.M)
        match = line.search(text)
        if not match:
            return f"{pad}{key}: {rendered}\n" + text
        comment = re.search(r"\s+#.*$", match.group("rest"))
        new_line = f"{pad}{key}: {rendered}{comment.group(0) if comment else ''}"
        return text[:match.start()] + new_line + text[match.end():]
    block = re.search(rf"^{pad}{re.escape(parents[0])}:[^\n]*\n((?:{pad}[ \t]+[^\n]*\n|\n)*)",
                      text, re.M)
    if not block:
        new = "".join(f"{'  ' * (depth + i)}{p}:\n" for i, p in enumerate(parents))
        new += f"{'  ' * (depth + len(parents))}{key}: {rendered}\n"
        kept = text.rstrip("\n")
        # Any blank lines that ended the text stay after the new lines.
        return (kept + "\n" if kept else "") + new + text[len(kept) + 1:]
    body = _set_in(block.group(1), depth + 1, parents[1:], key, rendered)
    return text[:block.start(1)] + body + text[block.end(1):]


def _get(cfg: dict, path) -> object:
    for key in path:
        cfg = cfg.get(key) if isinstance(cfg, dict) else None
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--data", help="base folder: sets the four folders to "
                                   "<data>/transcripts, work, out and log")
    for key in FOLDERS:
        ap.add_argument(f"--{key}", help=f"set {key} (overrides --data)")
    ap.add_argument("--model", help="the llm builder's model (and the definer's)")
    ap.add_argument("--definer-model", help="category-definer's model, if different")
    ap.add_argument("--ollama-url")
    ap.add_argument("--tool-folder", action="append", default=[],
                    help="add a folder holding pdftotext (repeatable)")
    ap.add_argument("--no-mkdir", action="store_true", help="do not create the folders")
    args = ap.parse_args(argv)

    path: Path = args.config
    created = not path.exists()
    text = path.read_text(encoding="utf-8") if path.exists() else TEMPLATE

    wanted = {}
    for key, sub in FOLDERS.items():
        given = getattr(args, key.replace("-", "_"))
        if given or args.data:
            wanted[key] = path_value(given or str(Path(args.data).expanduser() / sub))
    for key, value in wanted.items():
        text = set_top(text, key, scalar(value))

    # (path in config.yaml, value): the llm builder, then the definer.
    nested = [(("category-builder", "llm", k), v)
              for k, v in (("model", args.model), ("ollama-url", args.ollama_url)) if v]
    nested += [(("category-definer", k), v)
               for k, v in (("model", args.definer_model or args.model),
                            ("ollama-url", args.ollama_url)) if v]
    for (*parents, key), value in nested:
        text = set_nested(text, parents, key, scalar(value))

    if args.tool_folder:
        current = (yaml.safe_load(text) or {}).get("tool-folders") or []
        current = [current] if isinstance(current, str) else list(current)
        for folder in map(path_value, args.tool_folder):
            if folder not in current:
                current.append(folder)
        text = set_top(text, "tool-folders", "[" + ", ".join(scalar(f) for f in current) + "]")

    # Re-read before replacing anything: valid YAML, and the values we meant.
    parsed = yaml.safe_load(text) or {}
    problems = [k for k, v in wanted.items() if parsed.get(k) != v]
    problems += [".".join(path) for path, v in nested if _get(parsed, path) != v]
    if problems:
        print(f"refusing to write {path}: {', '.join(problems)} did not come out as intended")
        return 1
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

    if not args.no_mkdir:
        for key in FOLDERS:
            if parsed.get(key):
                Path(str(parsed[key])).expanduser().mkdir(parents=True, exist_ok=True)

    print(f"{'created' if created else 'updated'} {path}")
    for key in FOLDERS:
        print(f"  {key}: {parsed.get(key)}")
    for path in (("category-builder", "llm", "model"), ("category-builder", "llm", "ollama-url"),
                 ("category-definer", "model")):
        print(f"  {'.'.join(path)}: {_get(parsed, path)}")
    if parsed.get("tool-folders"):
        print(f"  tool-folders: {parsed['tool-folders']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
