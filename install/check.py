"""Look at this machine and report what gpa-tools needs. Changes nothing.

    python3 install/check.py            # JSON, for the install skill
    python3 install/check.py --text     # the same, for a person

Standard library only, so it runs before the venv exists, with whatever
Python is on the machine (3.8 or newer).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV = PROJECT_ROOT / ".venv"
TOOLS_DIR = PROJECT_ROOT / ".tools"  # micromamba + its env, made by get_tools.py
# The jev builder's defaults (jev_category/builder.py DEFAULTS; repeated here
# because this file must run before the venv and PyYAML exist).
JEV_DEFAULTS = {"model": "jev-1.14.0", "api-url": "https://api.typesafe.ai/v1/systemone",
                "api-key-env": "GPA_TOOLS_KEY", "mode": "direct", "cloud-url": "",
                "cloud-token-env": "GPA_TOOLS_CLOUD_TOKEN"}
# The Pythons gpa-tools runs on, preferred first (a venv of either works).
PYTHON_SUPPORTED = ((3, 14), (3, 12))

def run(cmd, timeout=10) -> str:
    """stdout+stderr of a command, or "" if it is missing or fails to start."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout + proc.stderr).strip()


# ---- machine ---------------------------------------------------------------

def os_info() -> dict:
    system = platform.system()  # Linux, Darwin, Windows
    info = {"system": system, "release": platform.release(), "machine": platform.machine()}
    if system == "Linux":
        try:
            fields = dict(line.split("=", 1) for line in
                          Path("/etc/os-release").read_text(encoding="utf-8").splitlines() if "=" in line)
            info["distro"] = fields.get("PRETTY_NAME", "").strip('"')
        except OSError:
            pass
        info["wsl"] = "microsoft" in platform.release().lower()
        info["systemd_user"] = bool(shutil.which("systemctl")) and \
            run(["systemctl", "--user", "is-system-running"]) not in ("", "offline")
    elif system == "Darwin":
        info["apple_silicon"] = platform.machine() == "arm64"
    info["admin"] = is_admin()
    return info


def is_admin() -> bool:
    """Whether this process already runs as root/administrator (not whether sudo works)."""
    if os.name == "nt":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (OSError, AttributeError):
            return False
    return os.geteuid() == 0


def ram_gb() -> float | None:
    system = platform.system()
    try:
        if system == "Linux":
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 ** 2, 1)
        elif system == "Darwin":
            return round(int(run(["sysctl", "-n", "hw.memsize"])) / 1024 ** 3, 1)
        elif system == "Windows":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return round(status.ullTotalPhys / 1024 ** 3, 1)
    except (OSError, ValueError, AttributeError):
        pass
    return None


# ---- software --------------------------------------------------------------

def python_info() -> dict:
    found = {}
    names = [f"python{a}.{b}" for a, b in PYTHON_SUPPORTED] + ["python3", "python"]
    names += [f"py -{a}.{b}" for a, b in PYTHON_SUPPORTED]  # the Windows launcher
    for name in names:
        command, _, flag = name.partition(" ")
        path = shutil.which(command)
        if path:
            version = run([path, *([flag] if flag else []),
                           "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
            if re.fullmatch(r"\d+\.\d+\.\d+", version):
                found[name] = {"path": path, "version": version, "supported": supported(version)}
    venv_python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    venv = {"path": str(VENV), "exists": venv_python.exists(), "python": str(venv_python)}
    if venv["exists"]:
        venv["version"] = run([str(venv_python), "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
        venv["supported"] = supported(venv["version"])
        missing = run([str(venv_python), "-c",
                       "import importlib.util as u; "
                       "print(' '.join(m for m in ('yaml', 'flask', 'playwright', 'pytest') "
                       "if not u.find_spec(m)))"])
        venv["missing_packages"] = missing.split()
    return {"running": sys.version.split()[0], "candidates": found, "venv": venv,
            "uv": shutil.which("uv") or _user_bin("uv")}


def supported(version: str) -> bool:
    """"3.12.3" -> True: its major.minor is one of PYTHON_SUPPORTED."""
    return tuple(int(n) for n in version.split(".")[:2]) in PYTHON_SUPPORTED if version else False


def _user_bin(name: str) -> str | None:
    """A tool in the usual per-user install folders, even if not yet on PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    for folder in (Path.home() / ".local/bin", Path.home() / ".cargo/bin"):
        if (folder / exe).exists():
            return str(folder / exe)
    return None


def tool_folders(config: dict) -> list[str]:
    folders = config.get("tool-folders") or []
    return [folders] if isinstance(folders, str) else [str(f) for f in folders]


def tools_info(config: dict) -> dict:
    """pdftotext (poppler): on PATH or in tool-folders."""
    search = os.pathsep.join(tool_folders(config) + [os.environ.get("PATH", "")])
    found = {}
    for tool in ("pdftotext",):
        path = shutil.which(tool, path=search)
        entry = {"path": path}
        if path:
            first = run([path, "-v"]).splitlines()
            entry["version"] = first[0] if first else ""
        found[tool] = entry
    found["micromamba"] = {"path": shutil.which("micromamba") or _micromamba_local()}
    found["chrome"] = {"path": chrome_path()}
    found["msedge"] = {"path": edge_path()}
    return found


def chrome_path() -> str | None:
    """Google Chrome, which the definer's chatgpt and gemini steps drive
    (Playwright's channel="chrome" looks in these standard places)."""
    if sys.platform == "darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    elif os.name == "nt":
        candidates = [Path(os.environ[v]) / "Google/Chrome/Application/chrome.exe"
                      for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if os.environ.get(v)]
    else:
        candidates = [Path("/opt/google/chrome/chrome")]
    for path in candidates:
        if path.exists():
            return str(path)
    return shutil.which("google-chrome") or shutil.which("google-chrome-stable")


def edge_path() -> str | None:
    """Microsoft Edge, the definer's next choice when there is no Chrome."""
    if sys.platform == "darwin":
        candidates = [Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    elif os.name == "nt":
        candidates = [Path(os.environ[v]) / "Microsoft/Edge/Application/msedge.exe"
                      for v in ("PROGRAMFILES(X86)", "PROGRAMFILES") if os.environ.get(v)]
    else:
        candidates = [Path("/opt/microsoft/msedge/msedge")]
    for path in candidates:
        if path.exists():
            return str(path)
    return shutil.which("microsoft-edge") or shutil.which("microsoft-edge-stable")


def _micromamba_local() -> str | None:
    exe = TOOLS_DIR / ("micromamba.exe" if os.name == "nt" else "micromamba")
    return str(exe) if exe.exists() else None


# ---- TypeSafe (jev builder) ------------------------------------------------

def jev_models_url(api_url: str) -> str:
    """GET /v1/models next to the evaluation endpoint POST /v1/systemone."""
    return api_url.rstrip("/").rsplit("/", 1)[0] + "/models"


def probe_jev(api_url: str, key: str, timeout: float = 10) -> dict:
    """Whether TypeSafe answers and accepts the key; the model names it lists.
    Listing models is free (no tokens)."""
    url = jev_models_url(api_url)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}",
                                               "User-Agent": "gpa-tools"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            models = [m["name"] for m in json.loads(resp.read()).get("models", [])]
    except urllib.error.HTTPError as exc:
        return {"url": url, "reachable": True, "authorized": exc.code not in (401, 403),
                "error": f"HTTP {exc.code} {exc.reason}"}
    except (OSError, ValueError, KeyError) as exc:  # URLError is an OSError
        return {"url": url, "reachable": False, "error": str(getattr(exc, "reason", exc))}
    return {"url": url, "reachable": True, "authorized": True, "models": models}


def jev_model_ok(model: str, listed: list[str]) -> bool:
    """The API lists only aliases (jev-latest, ...) but accepts versioned IDs too."""
    return model in listed or bool(re.fullmatch(r"jev-\d+\.\d+\.\d+", model or ""))


def probe_cloud(cloud_url: str, token: str, timeout: float = 10) -> dict:
    """Whether the gpa-tools cloud server (gpa-tools-cloud) answers and accepts the
    client token; the Jev model it uses.  /v1/info asks Jev nothing (free)."""
    url = cloud_url.rstrip("/") + "/v1/info"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                               "User-Agent": "gpa-tools"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            reply = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"url": url, "reachable": True, "authorized": exc.code not in (401, 403),
                "error": f"HTTP {exc.code} {exc.reason}"}
    except (OSError, ValueError) as exc:
        return {"url": url, "reachable": False, "error": str(getattr(exc, "reason", exc))}
    return {"url": url, "reachable": True, "authorized": True, "model": reply.get("model")}


def cloud_token_here(env: str) -> tuple[str, str]:
    """(this laptop's client token, where it came from): $env, else ~/.gpa-tools/client_token
    (GPA_TOOLS_HOME), else ("", "pending ..." | "none").  Never prints the value."""
    if os.environ.get(env, "").strip():
        return os.environ[env].strip(), f"${env}"
    home = Path(os.environ.get("GPA_TOOLS_HOME") or Path.home() / ".gpa-tools").expanduser()
    try:
        token = (home / "client_token").read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token:
        return token, str(home / "client_token")
    if (home / "pending_token").exists():
        return "", "requested, waiting for approval (python -m jev_category.enroll status)"
    return "", "none (python -m jev_category.enroll request --email <you>)"


def jev_info(config: dict) -> dict:
    """The jev builder's settings, whether its key (direct mode) or client token
    (cloud mode) variable is set (never its value), and, when it is, whether the
    API or cloud server accepts it."""
    s = jev_settings(config)
    if str(s["mode"]).strip().lower() == "cloud":
        env = s["cloud-token-env"]
        token, where = cloud_token_here(env)
        info = {"mode": "cloud", "cloud-url": s["cloud-url"], "cloud-token-env": env,
                "token_set": bool(token), "token_from": where}
        if token and s["cloud-url"]:
            info["cloud"] = probe_cloud(s["cloud-url"], token)
        return info
    info = {"model": s["model"], "api-url": s["api-url"], "api-key-env": s["api-key-env"],
            "key_set": bool(os.environ.get(s["api-key-env"], "").strip())}
    if info["key_set"]:
        info["api"] = probe_jev(s["api-url"], os.environ[s["api-key-env"]].strip())
        if "models" in info["api"]:
            info["model_ok"] = jev_model_ok(s["model"], info["api"]["models"])
    return info


# ---- config ----------------------------------------------------------------

# Keys an older config.yaml may still have, and what replaced them.
OUTDATED_KEYS = {"llm-category": "delete it: the llm builder and Ollama were removed",
                 "pdf-folder": "input-folder: (PDFs in <input-folder>/transcripts)",
                 "letters-folder": "input-folder: (letters in <input-folder>/letters)",
                 "personal-folder": "input-folder: (profiles in <input-folder>/personal)"}
def jev_settings(config: dict) -> dict:
    """config.yaml's category-builder: jev: section, over the builder's defaults."""
    builders = config.get("category-builder") or {}
    own = (builders.get("jev") if isinstance(builders, dict) else None) or {}
    return {**JEV_DEFAULTS, **own}


DEFINER_DEFAULTS = {"mode": "dev", "retries": 1, "browser": "auto"}
DEFINER_MODES = {"dev": "chatgpt (window) -> gemini (window)",
                 "prod": "gemini (headless)"}


def definer_settings(config: dict) -> dict:
    """category-definer:, over its defaults."""
    own = config.get("category-definer")
    return {**DEFINER_DEFAULTS, **(own if isinstance(own, dict) else {})}


def read_config() -> tuple[dict, str]:
    """config.yaml as a dict (if PyYAML is importable here), and a status word."""
    path = PROJECT_ROOT / "config.yaml"
    if not path.exists():
        return {}, "missing"
    try:
        import yaml
    except ImportError:
        return {}, "present (not parsed: PyYAML not installed in this Python)"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}, "present"
    except (OSError, yaml.YAMLError) as exc:
        return {}, f"unreadable: {exc}"


def config_info(config: dict, status: str) -> dict:
    info = {"path": str(PROJECT_ROOT / "config.yaml"), "status": status}
    for key in ("input-folder", "work-folder", "out-folder", "log-folder"):
        if key in config:
            folder = Path(str(config[key])).expanduser()
            info[key] = {"value": str(config[key]), "exists": folder.is_dir()}
    info["active-category-builder"] = config.get("active-category-builder")
    builders = config.get("category-builder") or {}
    info["jev-configured"] = isinstance(builders, dict) and "jev" in builders
    definer = definer_settings(config)
    info["definer"] = {k: definer.get(k) for k in ("mode", "retries", "browser")}
    # An older config.yaml: those keys are ignored now; configure.py won't move them.
    info["outdated"] = {k: v for k, v in OUTDATED_KEYS.items() if k in config}
    if "provider" in definer:
        info["outdated"]["category-definer: provider"] = "category-definer: mode: dev or prod"
    # Ollama was removed: its settings are no longer read.
    own = config.get("category-definer") if isinstance(config.get("category-definer"), dict) else {}
    if isinstance(builders, dict) and "llm" in builders:
        info["outdated"]["category-builder: llm"] = "delete it: the llm builder and Ollama were removed"
    if config.get("active-category-builder") == "llm":
        info["outdated"]["active-category-builder: llm"] = "active-category-builder: jev"
    for key in ("model", "ollama-url"):
        if key in own:
            info["outdated"][f"category-definer: {key}"] = "delete it: the definer no longer uses Ollama"
    info["tool-folders"] = tool_folders(config)
    return info


def gather() -> dict:
    config, status = read_config()
    ram = ram_gb()
    free = shutil.disk_usage(Path.home()).free / 1024 ** 3
    return {
        "project": str(PROJECT_ROOT),
        "os": os_info(),
        "ram_gb": ram,
        "disk_free_gb": round(free, 1),
        "python": python_info(),
        "tools": tools_info(config),
        "jev": jev_info(config),
        "config": config_info(config, status),
    }


def as_text(r: dict) -> str:
    o, p, t, c = r["os"], r["python"], r["tools"], r["config"]
    yes = lambda v: "yes" if v else "no"  # noqa: E731
    lines = [
        f"OS        {o['system']} {o.get('distro') or o['release']} ({o['machine']})"
        + (" WSL" if o.get("wsl") else "") + (" [running as admin]" if o["admin"] else ""),
        f"RAM       {r['ram_gb']} GB    free disk {r['disk_free_gb']} GB",
        "python    " + (", ".join(f"{k} {v['version']}" + ("" if v["supported"] else " (unsupported)")
                                  for k, v in p["candidates"].items()) or "none found")
        + f"; supported: {', '.join('%d.%d' % v for v in PYTHON_SUPPORTED)}; uv: {p['uv'] or 'no'}",
        f"venv      {yes(p['venv']['exists'])}" + (f" ({p['venv'].get('version')}"
                                                   + ("" if p["venv"].get("supported") else " unsupported")
                                                   + ", missing: "
                                                   f"{' '.join(p['venv']['missing_packages']) or 'nothing'})"
                                                   if p["venv"]["exists"] else ""),
    ]
    for tool in ("pdftotext", "micromamba", "chrome", "msedge"):
        lines.append(f"{tool:<11}{t[tool]['path'] or 'not found'}")
    lines.append(f"config    {c['status']}; builder {c['active-category-builder']}")
    d = c["definer"]
    steps = DEFINER_MODES.get(d["mode"], "UNKNOWN MODE (use dev or prod)")
    lines.append(f"  definer: mode {d['mode']}: {steps}; retries {d['retries']}; browser {d['browser']}"
                 "; when every step fails, type the definition yourself")
    j = r["jev"]
    if j.get("mode") == "cloud":
        jev = (f"  jev: cloud at {j['cloud-url'] or 'NO cloud-url SET'}"
               f"; token: {j.get('token_from') or ('set' if j['token_set'] else 'not set')}")
        cloud = j.get("cloud")
        if cloud:
            jev += (f"; server accepts the token, Jev model {cloud['model']}" if "model" in cloud
                    else f"; server: {cloud['error']}")
        lines.append(jev)
    else:
        lines.append(_jev_direct_line(j, c))
    for key, instead in c["outdated"].items():
        lines.append(f"  OUTDATED: '{key}:' is from an older config.yaml; "
                     + (instead if instead.startswith("delete") else f"use {instead}"))
    for key in ("input-folder", "work-folder", "out-folder", "log-folder"):
        if key in c:
            lines.append(f"  {key}: {c[key]['value']}" + ("" if c[key]["exists"] else "  (does not exist)"))
    return "\n".join(lines)


def _jev_direct_line(j: dict, c: dict) -> str:
    jev = (f"  jev: {j['model']}" + ("" if c["jev-configured"] else " (default; no category-builder: jev: section)")
           + f"; key in ${j['api-key-env']}: {'set' if j['key_set'] else 'not set'}")
    api = j.get("api")
    if api:
        if "models" in api:
            jev += f"; API accepts the key, models: {', '.join(api['models'])}"
            jev += "" if j["model_ok"] else f"; MODEL {j['model']} NOT ACCEPTED"
        else:
            jev += f"; API: {api['error']}"
    return jev


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--text", action="store_true", help="human-readable summary instead of JSON")
    args = ap.parse_args(argv)
    report = gather()
    print(as_text(report) if args.text else json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
