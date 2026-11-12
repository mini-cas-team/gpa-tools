"""Create or update config.yaml for this machine.

    .venv/bin/python install/configure.py --data ~/gpa-tools-data

--jev-mode and --cloud-url point the jev builder at the gpa-tools cloud server
(the default) or, for development, TypeSafe itself (direct).
--jev-model and --jev-key-env set the jev builder (TypeSafe); the key itself
never goes in config.yaml, only the name of the variable holding it.
--active-builder picks the builder the Upload and View tabs start with.
--definer-mode picks category-definer's steps: dev (chatgpt -> gemini in
visible browser windows; needs a desktop) or prod (gemini headless).

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
FOLDERS = {"input-folder": "", "work-folder": "work", "out-folder": "out", "log-folder": "log"}
# Each gets one subfolder per component (folders.py COMPONENTS).
COMPONENT_FOLDERS = ("input-folder", "work-folder")
COMPONENTS = ("transcripts", "letters", "personal")
# A new config.yaml starts as this; main() then fills in what it is given.
TEMPLATE = """\
# gpa-tools configuration for this machine.
#
# Made by install/configure.py (or /install-gpa-tools in Claude Code); edit freely.

# --- folders ------------------------------------------------------------------
# Every component has a folder of raw input, <input-folder>/<component>/, and a
# folder of what the tools make from it, <work-folder>/<component>/:
#   transcripts  <name>.pdf             -> <name>.txt, <name>.common.json
#   letters      <key>_letter<N>.pdf    -> <key>_letter<N>.txt, letter_ranks.csv
#   personal     <name>.json            -> <name>.json
components: [transcripts, letters, personal]
input-folder: ~/gpa-tools-data # raw input, never written by a run
work-folder: ~/gpa-tools-data/work # made from the input; builders keep their files in <work-folder>/<builder>/
out-folder: ~/gpa-tools-data/out # published <cat>_gpa.csv, for other applications
log-folder: ~/gpa-tools-data/log # upload.log, jev_category.log, category_definer.log, browser_view.log

# --- View tab -------------------------------------------------------------------
min-category-courses: 4 # hide students with fewer category courses

# --- external tools -------------------------------------------------------------
# Folders holding pdftotext when it is not on PATH
# (the installer puts them in .tools/ when there is no admin access).
# tool-folders: []

# --- category builders --------------------------------------------------------
# python -m category_builder --category STEM [--builder jev] [--publish]
# Each reads the *.common.json in <work-folder>/transcripts and writes <cat>_gpa.csv and
# <cat>_courses.csv into <work-folder>/<builder>/. python -m out_builder copies
# <cat>_gpa.csv to out-folder (the Upload tab does that after each run).
active-category-builder: jev # the default in the Upload and View tabs
category-builder:
  jev: # TypeSafe's Jev scores each course 0-1, through the gpa-tools cloud server
    mode: cloud # cloud: the gpa-tools cloud server holds the TypeSafe key; direct: TypeSafe with the key in api-key-env (development)
    cloud-url: https://gpa-tools-jev.johnprofessional132.workers.dev # mode cloud: the server
    cloud-token-env: GPA_TOOLS_CLOUD_TOKEN # mode cloud: optional override; the token is ~/.gpa-tools/client_token (python -m jev_category.enroll)
    model: jev-1.14.0 # a version pins it; `jev-latest` follows new releases
    api-url: https://api.typesafe.ai/v1/systemone
    api-key-env: GPA_TOOLS_KEY # name of the env var holding the key; never put the key here
    threshold: 0.5 # a course is in the category when Jev's score is above this
    batch-size: 20 # courses per request

# --- category-definer: drafts a definition for the user to edit -------------------
# Tries the mode's steps in turn until one gives a definition. chatgpt and gemini
# are their free web chats, used anonymously (no account, no key) in the installed
# Chrome or Edge. When every step fails, type the definition yourself in the Upload tab.
# Every try is logged to <log-folder>/category_definer.log and shown in the Upload tab.
category-definer:
  mode: dev # dev: chatgpt -> gemini, in visible browser windows (needs a desktop)
  #           prod: gemini (headless, no window)
  retries: 1 # extra tries for each site
  browser-timeout: 120 # seconds to wait for a site's reply
  browser: auto # auto (Chrome, else Edge, else Playwright's Chromium), chrome, msedge, chromium
  # profile-folder: ~/gpa-tools-data/work/browser-profiles # default <work-folder>/browser-profiles

# --- category definitions (all builders) ------------------------------------
# Shared by every category builder; drafted by category-definer, edit freely.
# Changing a definition clears the jev builder's cached scores for it.
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


# (section, key): settings of the removed llm builder and Ollama, no longer read
OLD_OLLAMA_KEYS = [
    (("category-builder",), "llm"),
    (("category-definer",), "model"),
    (("category-definer",), "ollama-url"),
]


def _get(cfg: dict, path) -> object:
    for key in path:
        cfg = cfg.get(key) if isinstance(cfg, dict) else None
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--data", help="base folder: input-folder <data>, and "
                                   "<data>/work, out and log for the other three")
    for key in FOLDERS:
        ap.add_argument(f"--{key}", help=f"set {key} (overrides --data)")
    ap.add_argument("--jev-model", help="the jev builder's TypeSafe model, e.g. jev-1.14.0")
    ap.add_argument("--jev-key-env", help="name of the env var holding the TypeSafe API key")
    ap.add_argument("--jev-mode", choices=("cloud", "direct"),
                    help="cloud: the gpa-tools cloud server (a laptop token); direct: TypeSafe itself (dev)")
    ap.add_argument("--cloud-url", help="the gpa-tools cloud server, mode cloud")
    ap.add_argument("--active-builder", choices=("jev",),
                    help="the builder the Upload and View tabs start with")
    ap.add_argument("--definer-mode", choices=("dev", "prod"),
                    help="category-definer: dev = chatgpt -> gemini in browser "
                         "windows, prod = gemini headless")
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
            wanted[key] = path_value(given or str(Path(args.data).expanduser() / sub if sub
                                                  else Path(args.data).expanduser()))
    if args.active_builder:
        wanted["active-category-builder"] = args.active_builder
    for key, value in wanted.items():
        text = set_top(text, key, scalar(value))

    # (path in config.yaml, value): the jev builder, then the definer.
    nested = [(("category-builder", "jev", k), v)
               for k, v in (("mode", args.jev_mode), ("cloud-url", args.cloud_url),
                            ("model", args.jev_model), ("api-key-env", args.jev_key_env)) if v]
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
                base = Path(str(parsed[key])).expanduser()
                base.mkdir(parents=True, exist_ok=True)
                if key in COMPONENT_FOLDERS:
                    for component in parsed.get("components") or COMPONENTS:
                        (base / str(component)).mkdir(exist_ok=True)

    print(f"{'created' if created else 'updated'} {path}")
    for key in FOLDERS:
        print(f"  {key}: {parsed.get(key)}")
    print(f"  active-category-builder: {parsed.get('active-category-builder')}")
    for path in (("category-definer", "mode"), ("category-builder", "jev", "mode"),
                 ("category-builder", "jev", "cloud-url"), ("category-builder", "jev", "model"),
                 ("category-builder", "jev", "api-key-env")):
        print(f"  {'.'.join(path)}: {_get(parsed, path)}")
    if parsed.get("tool-folders"):
        print(f"  tool-folders: {parsed['tool-folders']}")
    if "provider" in (parsed.get("category-definer") or {}):
        print("  note: category-definer's 'provider:' is from an older config.yaml and is no longer "
              "read; 'mode:' picks the steps now. Delete it by hand.")
    for where, key in OLD_OLLAMA_KEYS:
        if key in (_get(parsed, where) or {}):
            print(f"  note: {'.'.join(where)}.{key} is from an older config.yaml; the llm builder "
                  f"and Ollama were removed, so it is no longer read. Delete it by hand.")
    if "llm-category" in parsed:
        print("  note: 'llm-category:' is from an older config.yaml and is no longer read. "
              "Delete it by hand.")
    if parsed.get("active-category-builder") == "llm":
        print("  note: active-category-builder llm was removed; jev is used. "
              "Set it with --active-builder jev.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
