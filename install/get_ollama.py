"""Install Ollama into the current user's account (no admin) and start it.

    .venv312/bin/python install/get_ollama.py --dry-run
    .venv312/bin/python install/get_ollama.py            # --rocm adds AMD GPU support (Linux)

Linux    the release archive (ollama-linux-<arch>.tar.zst) unpacked into
         ~/.local/ollama, linked from ~/.local/bin/ollama, and started as a
         systemd *user* service (or a background ``ollama serve`` without one).
macOS    Ollama.app unpacked into ~/Applications and opened.
Windows  OllamaSetup.exe, which installs per-user by default, run silently.

It stops without changing anything if a server already answers at the URL.
Run it with the venv's Python: on Linux without the ``zstd`` command it
unpacks with the ``zstandard`` package (``pip install zstandard``).
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check import DEFAULT_OLLAMA_URL, ollama_binary, os_info, probe_ollama  # noqa: E402

BASE = "https://ollama.com/download"
HOME = Path.home()
LINUX_DIR = HOME / ".local/ollama"
LINUX_LINK = HOME / ".local/bin/ollama"
UNIT = HOME / ".config/systemd/user/ollama.service"
UNIT_TEXT = """[Unit]
Description=Ollama (user service, installed by gpa-tools install/get_ollama.py)
After=network-online.target

[Service]
ExecStart={binary} serve
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""


def download(url: str, target: Path) -> None:
    print(f"downloading {url}", flush=True)
    with urllib.request.urlopen(url, timeout=120) as resp, target.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done, shown = 0, -1
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total and (pct := 100 * done // total) // 10 != shown:
                shown = pct // 10
                print(f"  {pct}% of {total / 1e9:.1f} GB", flush=True)


def unpack_zst(archive: Path, dest: Path) -> None:
    """A .tar.zst into dest: the zstd command, else Python's zstd support."""
    dest.mkdir(parents=True, exist_ok=True)
    if shutil.which("zstd"):
        with subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE) as proc:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                tar.extractall(dest, filter="data")  # no paths outside dest
        if proc.returncode:
            raise SystemExit("zstd failed to unpack the archive")
        return
    try:
        from compression import zstd  # Python 3.14+
        opener = zstd.open
    except ImportError:
        try:
            import zstandard
        except ImportError:
            raise SystemExit("no zstd support: run `.venv312/bin/pip install zstandard` "
                             "and use .venv312/bin/python for this script") from None
        opener = lambda p: zstandard.ZstdDecompressor().stream_reader(open(p, "rb"))  # noqa: E731
    with opener(archive) as raw, tarfile.open(fileobj=raw, mode="r|") as tar:
        tar.extractall(dest, filter="data")  # no paths outside dest


def wait_for(url: str, seconds: int = 60) -> bool:
    for _ in range(seconds):
        if probe_ollama(url, timeout=2)["reachable"]:
            return True
        time.sleep(1)
    return False


def linux(args, steps: list[str]) -> None:
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower())
    if not arch:
        raise SystemExit(f"no Ollama build for {platform.machine()}")
    names = [f"ollama-linux-{arch}"] + ([f"ollama-linux-{arch}-rocm"] if args.rocm else [])
    systemd = os_info().get("systemd_user")
    binary = LINUX_DIR / "bin/ollama"
    steps += [f"download {BASE}/{n}.tar.zst and unpack into {LINUX_DIR}" for n in names]
    steps.append(f"link {LINUX_LINK} -> {binary}")
    steps.append(f"write {UNIT} and `systemctl --user enable --now ollama`" if systemd
                 else f"start `{binary} serve` in the background (log: ~/.ollama/serve.log)")
    if args.dry_run:
        return

    if (LINUX_DIR / "lib/ollama").exists():
        shutil.rmtree(LINUX_DIR / "lib/ollama")  # as the official script does on upgrade
    with tempfile.TemporaryDirectory(dir=HOME / ".local") as tmp:
        for name in names:
            archive = Path(tmp) / f"{name}.tar.zst"
            download(f"{BASE}/{name}.tar.zst", archive)
            print(f"unpacking into {LINUX_DIR}", flush=True)
            unpack_zst(archive, LINUX_DIR)
            archive.unlink()
    LINUX_LINK.parent.mkdir(parents=True, exist_ok=True)
    if LINUX_LINK.is_symlink() or LINUX_LINK.exists():
        LINUX_LINK.unlink()
    LINUX_LINK.symlink_to(binary)

    if systemd:
        UNIT.parent.mkdir(parents=True, exist_ok=True)
        UNIT.write_text(UNIT_TEXT.format(binary=binary), encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "ollama"], check=True)
    else:
        log = HOME / ".ollama/serve.log"
        log.parent.mkdir(exist_ok=True)
        subprocess.Popen([str(binary), "serve"], stdout=log.open("ab"), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)


def macos(args, steps: list[str]) -> None:
    apps = HOME / "Applications"
    steps += [f"download {BASE}/Ollama-darwin.zip", f"unpack Ollama.app into {apps}",
              "open it (it starts the server; decline its offer to install the CLI with an admin password)"]
    if args.dry_run:
        return
    apps.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "Ollama-darwin.zip"
        download(f"{BASE}/Ollama-darwin.zip", archive)
        # ditto keeps the app's symlinks and code signature; zipfile would not.
        subprocess.run(["ditto", "-x", "-k", str(archive), str(apps)], check=True)
    subprocess.run(["open", str(apps / "Ollama.app")], check=True)


def windows(args, steps: list[str]) -> None:
    steps += [f"download {BASE}/OllamaSetup.exe (about 1.6 GB)",
              "run it silently; it installs into %LOCALAPPDATA%\\Programs\\Ollama for this user "
              "and starts Ollama, which then also starts at login"]
    if args.dry_run:
        return
    with tempfile.TemporaryDirectory() as tmp:
        setup = Path(tmp) / "OllamaSetup.exe"
        download(f"{BASE}/OllamaSetup.exe", setup)
        subprocess.run([str(setup), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"], check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=DEFAULT_OLLAMA_URL, help="where the server should answer")
    ap.add_argument("--rocm", action="store_true", help="Linux: also install AMD GPU (ROCm) support")
    ap.add_argument("--dry-run", action="store_true", help="say what would happen, change nothing")
    ap.add_argument("--force", action="store_true", help="install even if a server already answers")
    args = ap.parse_args(argv)

    if probe_ollama(args.url)["reachable"] and not args.force:
        print(f"an Ollama server already answers at {args.url} ({ollama_binary() or 'binary not found'}); "
              f"nothing to do")
        return 0

    steps: list[str] = []
    installer = {"Linux": linux, "Darwin": macos, "Windows": windows}.get(platform.system())
    if not installer:
        raise SystemExit(f"unsupported system: {platform.system()}")
    installer(args, steps)
    if args.dry_run:
        print("would:\n" + "\n".join(f"  {i}. {s}" for i, s in enumerate(steps, 1)))
        return 0

    print(f"waiting for the server at {args.url}…", flush=True)
    if not wait_for(args.url):
        print(f"installed ({ollama_binary()}), but nothing answers at {args.url} yet")
        return 1
    print(f"Ollama answers at {args.url}; binary: {ollama_binary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
