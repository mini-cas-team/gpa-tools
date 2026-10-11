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
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV = PROJECT_ROOT / ".venv312"
TOOLS_DIR = PROJECT_ROOT / ".tools"  # micromamba + its env, made by get_tools.py
DEFAULT_OLLAMA_URL = "http://localhost:11434"
PYTHON_WANTED = (3, 12)

# Download sizes, for the free-disk check.
MODEL_GB = {"qwen2.5:1.5b": 1.0, "qwen2.5:3b": 1.9, "qwen2.5:7b": 4.7}


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


def gpus() -> list[dict]:
    """GPUs Ollama can use, with memory in GB when it can be read reliably."""
    found = []
    smi = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    for line in smi.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1].isdigit():
            found.append({"vendor": "nvidia", "name": parts[0],
                          "vram_gb": round(int(parts[1]) / 1024, 1)})
    system = platform.system()
    if system == "Linux":  # AMD (amdgpu driver) reports VRAM in sysfs
        for total in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
            try:
                gb = round(int(total.read_text().strip()) / 1024 ** 3, 1)
            except (OSError, ValueError):
                continue
            if gb >= 1:  # integrated GPUs report a small carve-out
                found.append({"vendor": "amd", "name": total.parent.name, "vram_gb": gb})
    elif system == "Darwin" and platform.machine() == "arm64":
        # Apple Silicon shares system memory with the GPU.
        found.append({"vendor": "apple", "name": "Apple Silicon (unified memory)", "vram_gb": ram_gb()})
    elif system == "Windows" and not found:
        names = run(["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_VideoController).Name"], timeout=20)
        for name in filter(None, (n.strip() for n in names.splitlines())):
            # AdapterRAM is capped at 4 GB and unreliable, so memory stays unknown.
            vendor = "amd" if re.search(r"AMD|Radeon", name, re.I) else "other"
            found.append({"vendor": vendor, "name": name, "vram_gb": None})
    return found


def recommend_model(ram: float | None, gpu_list: list[dict]) -> dict:
    """qwen2.5 sized to the machine. A CPU-only machine gets 3b even with lots
    of RAM: 7b answers about 2.5 times slower, and a full run asks ~400 questions."""
    best_gpu = max((g["vram_gb"] or 0 for g in gpu_list if g["vendor"] in ("nvidia", "amd")),
                   default=0)
    apple = next((g for g in gpu_list if g["vendor"] == "apple"), None)
    if best_gpu >= 8:
        return {"model": "qwen2.5:7b", "reason": f"GPU with {best_gpu} GB of memory"}
    if apple and (apple["vram_gb"] or 0) >= 16:
        return {"model": "qwen2.5:7b", "reason": f"Apple Silicon with {apple['vram_gb']} GB"}
    if ram is None:
        return {"model": "qwen2.5:3b", "reason": "RAM unknown; the middle size"}
    if ram >= 8:
        where = f"GPU with {best_gpu} GB (too small for 7b)" if best_gpu else "no usable GPU"
        return {"model": "qwen2.5:3b", "reason": f"{ram} GB RAM, {where}"}
    return {"model": "qwen2.5:1.5b", "reason": f"only {ram} GB RAM"}


# ---- software --------------------------------------------------------------

def python_info() -> dict:
    found = {}
    for name in ("python3.12", "python3", "python", "py"):
        path = shutil.which(name)
        if path:
            args = [path, "-3.12"] if name == "py" else [path]
            version = run(args + ["-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
            if re.fullmatch(r"\d+\.\d+\.\d+", version):
                found[name] = {"path": path, "version": version}
    venv_python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    venv = {"path": str(VENV), "exists": venv_python.exists(), "python": str(venv_python)}
    if venv["exists"]:
        venv["version"] = run([str(venv_python), "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
        missing = run([str(venv_python), "-c",
                       "import importlib.util as u; "
                       "print(' '.join(m for m in ('yaml', 'flask', 'pytest') "
                       "if not u.find_spec(m)))"])
        venv["missing_packages"] = missing.split()
    return {"running": sys.version.split()[0], "candidates": found, "venv": venv,
            "uv": shutil.which("uv") or _user_bin("uv")}


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
    return found


def _micromamba_local() -> str | None:
    exe = TOOLS_DIR / ("micromamba.exe" if os.name == "nt" else "micromamba")
    return str(exe) if exe.exists() else None


def ollama_binary() -> str | None:
    on_path = shutil.which("ollama")
    if on_path:
        return on_path
    home = Path.home()
    candidates = [home / ".local/ollama/bin/ollama", home / ".local/bin/ollama",
                  home / "Applications/Ollama.app/Contents/Resources/ollama",
                  Path("/Applications/Ollama.app/Contents/Resources/ollama")]
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        candidates.insert(0, local / "Programs/Ollama/ollama.exe")
    return next((str(c) for c in candidates if c.exists()), None)


def probe_ollama(url: str, timeout: float = 5) -> dict:
    """Whether an Ollama server answers at url, its version and models."""
    url = url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=timeout) as resp:
            version = json.loads(resp.read()).get("version")
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as resp:
            models = [m["name"] for m in json.loads(resp.read()).get("models", [])]
    except (OSError, ValueError, KeyError) as exc:  # URLError is an OSError
        return {"url": url, "reachable": False, "error": str(getattr(exc, "reason", exc))}
    return {"url": url, "reachable": True, "version": version, "models": models}


def ollama_info(config: dict) -> dict:
    binary = ollama_binary()
    info = {"binary": binary, "version": run([binary, "--version"]) if binary else None}
    env_host = os.environ.get("OLLAMA_HOST")
    if env_host:
        info["OLLAMA_HOST"] = env_host
    configured = (llm_settings(config).get("ollama-url"), definer_settings(config).get("ollama-url"))
    urls = [DEFAULT_OLLAMA_URL]
    for u in configured:
        if u and u.rstrip("/") not in [x.rstrip("/") for x in urls]:
            urls.append(u)
    info["servers"] = [probe_ollama(u) for u in urls]
    return info


# ---- config ----------------------------------------------------------------

# Keys an older config.yaml may still have, and what replaced them.
OUTDATED_KEYS = {"llm-category": "category-builder: llm: (model, ollama-url)"}


def llm_settings(config: dict) -> dict:
    """config.yaml's category-builder: llm: section."""
    builders = config.get("category-builder") or {}
    return (builders.get("llm") if isinstance(builders, dict) else None) or {}


def definer_settings(config: dict) -> dict:
    """category-definer:, with the keys it leaves out taken from the llm builder."""
    own = config.get("category-definer")
    llm = {k: v for k, v in llm_settings(config).items() if k in ("model", "ollama-url")}
    return {"provider": "ollama", **llm, **(own if isinstance(own, dict) else {})}


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
    for key in ("pdf-folder", "work-folder", "out-folder", "log-folder"):
        if key in config:
            folder = Path(str(config[key])).expanduser()
            info[key] = {"value": str(config[key]), "exists": folder.is_dir()}
    llm = llm_settings(config)
    info["model"] = llm.get("model")
    info["ollama-url"] = llm.get("ollama-url")
    info["active-category-builder"] = config.get("active-category-builder")
    definer = definer_settings(config)
    info["definer"] = {k: definer.get(k) for k in ("provider", "model", "ollama-url")}
    # An older config.yaml: those keys are ignored now; configure.py won't move them.
    info["outdated"] = {k: v for k, v in OUTDATED_KEYS.items() if k in config}
    info["tool-folders"] = tool_folders(config)
    return info


def gather() -> dict:
    config, status = read_config()
    ram = ram_gb()
    gpu_list = gpus()
    recommended = recommend_model(ram, gpu_list)
    free = shutil.disk_usage(Path.home()).free / 1024 ** 3
    return {
        "project": str(PROJECT_ROOT),
        "os": os_info(),
        "ram_gb": ram,
        "gpus": gpu_list,
        "disk_free_gb": round(free, 1),
        "recommended_model": {**recommended, "download_gb": MODEL_GB[recommended["model"]],
                              "fits_disk": free > MODEL_GB[recommended["model"]] + 2},
        "python": python_info(),
        "tools": tools_info(config),
        "ollama": ollama_info(config),
        "config": config_info(config, status),
    }


def as_text(r: dict) -> str:
    o, p, t, ol, c = r["os"], r["python"], r["tools"], r["ollama"], r["config"]
    yes = lambda v: "yes" if v else "no"  # noqa: E731
    lines = [
        f"OS        {o['system']} {o.get('distro') or o['release']} ({o['machine']})"
        + (" WSL" if o.get("wsl") else "") + (" [running as admin]" if o["admin"] else ""),
        f"RAM       {r['ram_gb']} GB    free disk {r['disk_free_gb']} GB",
        "GPU       " + (", ".join(f"{g['name']} ({g['vram_gb'] or '?'} GB)" for g in r["gpus"]) or "none usable"),
        f"model     {r['recommended_model']['model']}  ({r['recommended_model']['reason']})",
        "python    " + (", ".join(f"{k} {v['version']}" for k, v in p["candidates"].items()) or "none found")
        + f"; uv: {p['uv'] or 'no'}",
        f"venv      {yes(p['venv']['exists'])}" + (f" ({p['venv'].get('version')}, missing: "
                                                   f"{' '.join(p['venv']['missing_packages']) or 'nothing'})"
                                                   if p["venv"]["exists"] else ""),
    ]
    for tool in ("pdftotext", "micromamba"):
        lines.append(f"{tool:<11}{t[tool]['path'] or 'not found'}")
    lines.append(f"ollama    {ol['binary'] or 'not found'}" + (f" ({ol['version']})" if ol["version"] else ""))
    for s in ol["servers"]:
        lines.append(f"  {s['url']}: " + (f"answers, version {s['version']}, models: {', '.join(s['models']) or 'none'}"
                                          if s["reachable"] else f"no answer ({s['error']})"))
    lines.append(f"config    {c['status']}; builder {c['active-category-builder']}; "
                 f"llm model {c['model']}; ollama-url {c['ollama-url']}")
    d = c["definer"]
    lines.append(f"  definer: {d['provider']} {d['model']} at {d['ollama-url']}")
    for key, instead in c["outdated"].items():
        lines.append(f"  OUTDATED: '{key}:' is no longer read; use {instead}")
    for key in ("pdf-folder", "work-folder", "out-folder", "log-folder"):
        if key in c:
            lines.append(f"  {key}: {c[key]['value']}" + ("" if c[key]["exists"] else "  (does not exist)"))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--text", action="store_true", help="human-readable summary instead of JSON")
    args = ap.parse_args(argv)
    report = gather()
    print(as_text(report) if args.text else json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
