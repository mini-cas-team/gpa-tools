"""Create or update config.yaml for this machine.

    .venv312/bin/python install/configure.py --data ~/gpa-tools-data \\
        --model qwen2.5:3b --ollama-url http://localhost:11434

--model sets both Ollama models in category-builder: llm:,
category-build-model (the llm builder's answers) and category-definition-model
(category-definer's ollama step); --definer-model then gives the definer its
own. --ollama-url is where both run.
--jev-model and --jev-key-env set the jev builder (TypeSafe); the key itself
never goes in config.yaml, only the name of the variable holding it.
--active-builder picks the builder the Upload and View tabs start with.
--definer-mode picks category-definer's steps: dev (chatgpt -> gemini ->
ollama in visible browser windows; needs a desktop) or prod (gemini headless
-> ollama).

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
log-folder: ~/gpa-tools-data/log # upload.log, llm_category.log, jev_category.log, browser_view.log

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
    category-build-model: qwen2.5:3b # yes/no per course; the installer picks one for this machine
    category-definition-model: qwen2.5:3b # category-definer's ollama step; one call per category, so a bigger model is affordable
    ollama-url: http://localhost:11434 # any model in `ollama list` there
  jev: # TypeSafe's Jev scores each course 0-1 (hosted; needs an API key)
    model: jev-1.13.0 # a version pins it; `jev-latest` follows new releases
    api-url: https://api.typesafe.ai/v1/systemone
    api-key-env: GPA_TOOLS_KEY # name of the env var holding the key; never put the key here
    threshold: 0.5 # a course is in the category when Jev's score is above this
    batch-size: 20 # courses per request

# --- category-definer: drafts a definition for the user to edit -------------------
# Tries the mode's steps in turn until one gives a definition. chatgpt and gemini
# are their free web chats, used anonymously (no account, no key) in the installed
# Chrome or Edge; ollama is category-builder: llm: category-definition-model. Every try is logged to
# <log-folder>/category_definer.log and shown in the Upload tab.
category-definer:
  mode: dev # dev: chatgpt -> gemini -> ollama, in visible browser windows (needs a desktop)
  #           prod: gemini (headless, no window) -> ollama
  retries: 1 # extra tries for chatgpt and gemini (ollama is tried once)
  browser-timeout: 120 # seconds to wait for a site's reply
  browser: auto # auto (Chrome, else Edge, else Playwright's Chromium), chrome, msedge, chromium
  # profile-folder: ~/gpa-tools-data/work/browser-profiles # default <work-folder>/browser-profiles

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


# (section, key, what replaces it): read only while the new key is missing
OLD_MODEL_KEYS = [
    (("category-builder", "llm"), "model", "category-build-model / category-definition-model"),
    (("category-definer",), "model", "category-builder.llm.category-definition-model"),
    (("category-definer",), "ollama-url", "category-builder.llm.ollama-url"),
]


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
    ap.add_argument("--model", help="category-build-model and category-definition-model")
    ap.add_argument("--definer-model", help="category-definition-model, if different")
    ap.add_argument("--ollama-url")
    ap.add_argument("--jev-model", help="the jev builder's TypeSafe model, e.g. jev-1.13.0")
    ap.add_argument("--jev-key-env", help="name of the env var holding the TypeSafe API key")
    ap.add_argument("--active-builder", choices=("llm", "jev"),
                    help="the builder the Upload and View tabs start with")
    ap.add_argument("--definer-mode", choices=("dev", "prod"),
                    help="category-definer: dev = chatgpt -> gemini -> ollama in browser "
                         "windows, prod = gemini headless -> ollama")
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
    if args.active_builder:
        wanted["active-category-builder"] = args.active_builder
    for key, value in wanted.items():
        text = set_top(text, key, scalar(value))

    # (path in config.yaml, value): the llm builder's two models, then jev.
    nested = [(("category-builder", "llm", k), v)
              for k, v in (("category-build-model", args.model),
                           ("category-definition-model", args.definer_model or args.model),
                           ("ollama-url", args.ollama_url)) if v]
    nested += [(("category-builder", "jev", k), v)
               for k, v in (("model", args.jev_model), ("api-key-env", args.jev_key_env)) if v]
    if args.definer_mode:
        nested.append((("category-definer", "mode"), args.definer_mode))
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
    print(f"  active-category-builder: {parsed.get('active-category-builder')}")
    for path in (("category-builder", "llm", "category-build-model"),
                 ("category-builder", "llm", "category-definition-model"),
                 ("category-builder", "llm", "ollama-url"),
                 ("category-definer", "mode"), ("category-builder", "jev", "model"),
                 ("category-builder", "jev", "api-key-env")):
        print(f"  {'.'.join(path)}: {_get(parsed, path)}")
    if parsed.get("tool-folders"):
        print(f"  tool-folders: {parsed['tool-folders']}")
    if "provider" in (parsed.get("category-definer") or {}):
        print("  note: category-definer's 'provider:' is from an older config.yaml and is no longer "
              "read; 'mode:' picks the steps now. Delete it by hand.")
    for where, key, instead in OLD_MODEL_KEYS:
        if key in (_get(parsed, where) or {}):
            print(f"  note: {'.'.join(where)}.{key} is from an older config.yaml; "
                  f"{instead} replaces it. Delete it by hand.")
    if "llm-category" in parsed:
        print("  note: 'llm-category:' is from an older config.yaml and is no longer read; "
              "its settings now live under category-builder: llm:. Delete it by hand.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
