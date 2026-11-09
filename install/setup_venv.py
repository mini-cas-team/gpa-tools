"""Make the project's .venv with the best Python on this machine and install
the packages. Standard library only, so it runs with whatever Python a fresh
clone finds (3.8 or newer).

    python3 install/setup_venv.py --dry-run     # what it would do; changes nothing
    python3 install/setup_venv.py               # do it
    python3 install/setup_venv.py --rebuild     # replace a .venv of an unsupported Python
    python3 install/setup_venv.py --python /usr/bin/python3.12   # that one, if supported

gpa-tools runs on check.PYTHON_SUPPORTED (3.14, else 3.12). The choice:

    .venv of a supported Python, packages there   ok: nothing to do
    .venv of a supported Python, packages missing  install them into it
    .venv of an unsupported Python                 stop (exit 2) unless --rebuild
    no .venv, a supported Python found             <it> -m venv .venv, then pip install
    no supported Python, uv found                  uv venv --python 3.14 .venv, uv pip install
    none of these                                  stop (exit 3) with how to get uv

It never installs uv or a Python itself (that writes outside the project) and
never needs admin. Exit 0 when .venv is ready, with its Python's version last.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import check  # this folder is on sys.path when run as a script

REQUIREMENTS = check.PROJECT_ROOT / "requirements.txt"
PACKAGES = ["-r", str(REQUIREMENTS), "pytest"]  # pytest: the install is verified by the tests
VENV_PYTHON = check.VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
UV_HELP = ("No Python " + " or ".join("%d.%d" % v for v in check.PYTHON_SUPPORTED) + " and no uv. "
           "Install uv for this user (no admin), then run this again:\n"
           "  Linux/macOS: curl -LsSf https://astral.sh/uv/install.sh | sh\n"
           '  Windows:     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"')


def _version(version: str) -> tuple:
    return tuple(int(n) for n in version.split(".")[:2])


def best_python(candidates: dict) -> tuple[str, dict] | None:
    """(name, candidate) of the most preferred supported Python, or None.
    candidates is check.python_info()["candidates"]: {"python3.12": {path, version, supported}}."""
    ok = [(name, c) for name, c in candidates.items() if c.get("supported")]
    ok.sort(key=lambda nc: check.PYTHON_SUPPORTED.index(_version(nc[1]["version"])))
    return ok[0] if ok else None


def interpreter(name: str, candidate: dict) -> list[str]:
    """The command that runs a candidate: its path, plus "-3.12" for the py launcher."""
    flag = name.partition(" ")[2]
    return [candidate["path"]] + ([flag] if flag else [])


def plan(info: dict, rebuild: bool = False, python: dict | None = None) -> dict:
    """What to do, from check.python_info(): {"action", "why", "python", "commands"}.
    ``python`` ({"path", "version", "supported"}) is the user's own pick instead of the best one.

    actions: ok, install, create, uv, rebuild-needed, no-python; "remove": true when
    the old .venv is deleted first (rebuild)."""
    venv = info["venv"]
    if python is not None and not python.get("supported"):
        return {"action": "no-python", "python": python.get("version") or None, "commands": [],
                "why": f"{python['path']} is " + (f"Python {python['version']}, not supported"
                                                 if python.get("version") else "not a Python that runs")}
    pip = [str(VENV_PYTHON), "-m", "pip", "install", *PACKAGES]
    remove = False
    if venv["exists"] and python is None:
        if venv.get("supported"):
            if not venv.get("missing_packages"):
                return {"action": "ok", "why": f".venv is Python {venv['version']} with every package",
                        "python": venv["version"], "commands": []}
            return {"action": "install", "python": venv["version"], "commands": [pip],
                    "why": f".venv is Python {venv['version']}; missing {' '.join(venv['missing_packages'])}"}
        if not rebuild:
            return {"action": "rebuild-needed", "python": venv.get("version"), "commands": [],
                    "why": f".venv is Python {venv.get('version') or '(broken)'}, not "
                           + " or ".join("%d.%d" % v for v in check.PYTHON_SUPPORTED)
                           + ": run again with --rebuild to replace it"}
        remove = True
    elif venv["exists"]:  # --python given: replace the venv only if it is another version
        if venv.get("version") and _version(venv["version"]) == _version(python["version"]):
            return plan(info, rebuild)
        if not rebuild:
            return {"action": "rebuild-needed", "python": venv.get("version"), "commands": [],
                    "why": f".venv is Python {venv.get('version')}; run again with --rebuild "
                           f"to replace it with {python['version']}"}
        remove = True

    if python is not None:
        found = ("--python", python)
    else:
        found = best_python(info["candidates"])
    if found:
        name, c = found
        command = [python["path"]] if python is not None else interpreter(name, c)
        return {"action": "create", "python": c["version"], "remove": remove,
                "why": f"{' '.join(command)} is Python {c['version']}",
                "commands": [command + ["-m", "venv", str(check.VENV)], pip]}
    if info.get("uv"):
        uv = info["uv"]
        want = "%d.%d" % check.PYTHON_SUPPORTED[0]
        return {"action": "uv", "python": want, "remove": remove,
                "why": f"no supported Python on this machine; uv ({uv}) fetches Python {want} for this user",
                "commands": [[uv, "venv", "--python", want, str(check.VENV)],
                             [uv, "pip", "install", "--python", str(check.VENV), *PACKAGES]]}
    return {"action": "no-python", "python": None, "commands": [], "why": UV_HELP}


def _probe(path: str) -> dict:
    version = check.run([path, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
    return {"path": path, "version": version, "supported": check.supported(version)}


def _show(cmd: list[str]) -> str:
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    ap.add_argument("--rebuild", action="store_true", help="replace a .venv of an unsupported (or another) Python")
    ap.add_argument("--python", help="use this interpreter (it must be a supported version)")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    args = ap.parse_args(argv)

    info = check.python_info()
    python = None
    if args.python:
        path = shutil.which(args.python) or args.python
        python = _probe(path)
    p = plan(info, args.rebuild, python)
    if args.json:
        print(json.dumps(p, indent=2))
    else:
        found = ", ".join(f"{k} {v['version']}" + ("" if v["supported"] else " (unsupported)")
                          for k, v in info["candidates"].items()) or "none"
        print(f"Pythons found: {found}; uv: {info['uv'] or 'no'}")
        print(f"{p['action']}: {p['why']}")
        if p.get("remove"):
            print(f"  remove {check.VENV}")
        for cmd in p["commands"]:
            print("  " + _show(cmd))
    if p["action"] == "rebuild-needed":
        return 2
    if p["action"] == "no-python":
        return 3
    if args.dry_run or not p["commands"]:
        return 0

    if p.get("remove") and check.VENV.exists():
        shutil.rmtree(check.VENV)
    commands = list(p["commands"])
    while commands:
        cmd = commands.pop(0)
        print(f"$ {_show(cmd)}", flush=True)
        if not subprocess.call(cmd, cwd=check.PROJECT_ROOT):
            continue
        if cmd[-3:] == ["-m", "venv", str(check.VENV)]:
            # e.g. Debian/Ubuntu without python3.X-venv: "ensurepip is not available"
            shutil.rmtree(check.VENV, ignore_errors=True)  # a half-made venv has no pip
            fallback = plan({**info, "candidates": {}, "venv": {"exists": False}})
            if fallback["action"] == "uv":
                print(f"venv failed; {fallback['why']}", flush=True)
                commands = list(fallback["commands"])
                continue
            print("venv failed. On Debian/Ubuntu \"ensurepip is not available\" means the "
                  "python3.X-venv apt package is missing (needs admin). Install uv for this "
                  "user instead and run this again; it then uses uv.\n" + UV_HELP, file=sys.stderr)
        return 1
    after = check.python_info()["venv"]
    if not after["exists"] or after.get("missing_packages"):
        print(f".venv is not ready: missing {' '.join(after.get('missing_packages') or ['the venv'])}",
              file=sys.stderr)
        return 1
    print(f".venv ready: Python {after['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
