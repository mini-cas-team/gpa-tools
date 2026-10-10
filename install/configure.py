"""Create or update config.yaml for this machine.

    .venv312/bin/python install/configure.py --data ~/gpa-tools-data \\
        --model qwen2.5:3b --ollama-url http://localhost:11434

Without config.yaml, it is created from config.example.yaml. Only the keys
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
EXAMPLE = PROJECT_ROOT / "config.example.yaml"
FOLDERS = {"pdf-folder": "transcripts", "work-folder": "work", "out-folder": "out", "log-folder": "log"}


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


def set_nested(text: str, parent: str, key: str, rendered: str) -> str:
    """Set ``parent: {key: ...}`` (two-space indent), adding either if missing."""
    block = re.search(rf"^{re.escape(parent)}:[^\n]*\n((?:[ \t]+[^\n]*\n|\n)*)", text, re.M)
    if not block:
        return text.rstrip("\n") + f"\n{parent}:\n  {key}: {rendered}\n"
    body = block.group(1)
    line = re.compile(rf"^(?P<indent>[ \t]+){re.escape(key)}:(?P<rest>.*)$", re.M)
    match = line.search(body)
    if match:
        comment = re.search(r"\s+#.*$", match.group("rest"))
        new_line = f"{match.group('indent')}{key}: {rendered}{comment.group(0) if comment else ''}"
        body = body[:match.start()] + new_line + body[match.end():]
    else:
        body = f"  {key}: {rendered}\n" + body
    return text[:block.start(1)] + body + text[block.end(1):]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--data", help="base folder: sets the four folders to "
                                   "<data>/transcripts, work, out and log")
    for key in FOLDERS:
        ap.add_argument(f"--{key}", help=f"set {key} (overrides --data)")
    ap.add_argument("--model")
    ap.add_argument("--ollama-url")
    ap.add_argument("--tool-folder", action="append", default=[],
                    help="add a folder holding pdftotext/tesseract (repeatable)")
    ap.add_argument("--no-mkdir", action="store_true", help="do not create the folders")
    args = ap.parse_args(argv)

    path: Path = args.config
    created = not path.exists()
    text = (path if path.exists() else EXAMPLE).read_text(encoding="utf-8")

    wanted = {}
    for key, sub in FOLDERS.items():
        given = getattr(args, key.replace("-", "_"))
        if given or args.data:
            wanted[key] = path_value(given or str(Path(args.data).expanduser() / sub))
    for key, value in wanted.items():
        text = set_top(text, key, scalar(value))

    llm = {k: v for k, v in (("model", args.model), ("ollama-url", args.ollama_url)) if v}
    for key, value in llm.items():
        text = set_nested(text, "llm-category", key, scalar(value))

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
    problems += [k for k, v in llm.items() if (parsed.get("llm-category") or {}).get(k) != v]
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
    llm_now = parsed.get("llm-category") or {}
    print(f"  model: {llm_now.get('model')}\n  ollama-url: {llm_now.get('ollama-url')}")
    if parsed.get("tool-folders"):
        print(f"  tool-folders: {parsed['tool-folders']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
