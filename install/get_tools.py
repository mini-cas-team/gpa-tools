"""Install pdftotext/pdftoppm (poppler) and tesseract without admin rights.

    python3 install/get_tools.py              # poppler + tesseract
    python3 install/get_tools.py --no-ocr     # poppler only (enough for upload/llm_category)
    python3 install/get_tools.py --dry-run

Downloads micromamba (one file) into .tools/, then makes a conda-forge
environment at .tools/env. Everything stays inside the project folder, which
is git-ignored; delete .tools/ to remove it. The last line printed is the
folder holding the tools, for ``configure.py --tool-folder``.

Standard library only. Works on Linux, macOS and Windows.
"""

from __future__ import annotations

import argparse
import io
import os
import platform
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS = PROJECT_ROOT / ".tools"
ENV = TOOLS / "env"
MICROMAMBA_URL = "https://micro.mamba.pm/api/micromamba/{platform}/latest"


def conda_platform() -> str:
    system, machine = platform.system(), platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if system == "Linux":
        return "linux-aarch64" if arm else "linux-64"
    if system == "Darwin":
        return "osx-arm64" if arm else "osx-64"
    if system == "Windows":
        return "win-64"
    raise SystemExit(f"unsupported system: {system} {machine}")


def micromamba_path() -> Path:
    return TOOLS / ("micromamba.exe" if os.name == "nt" else "micromamba")


def bin_folder() -> Path:
    """Where conda puts executables: Library/bin on Windows, bin elsewhere."""
    return ENV / ("Library/bin" if os.name == "nt" else "bin")


def fetch_micromamba() -> Path:
    exe = micromamba_path()
    if exe.exists():
        return exe
    url = MICROMAMBA_URL.format(platform=conda_platform())
    print(f"downloading micromamba from {url}", flush=True)
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    member = "Library/bin/micromamba.exe" if os.name == "nt" else "bin/micromamba"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as archive:
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit(f"{member} not found in the micromamba download")
        TOOLS.mkdir(exist_ok=True)
        exe.write_bytes(source.read())
    exe.chmod(0o755)
    return exe


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-ocr", action="store_true", help="skip tesseract (only gpa_ana's scanned PDFs need it)")
    ap.add_argument("--dry-run", action="store_true", help="say what would happen, change nothing")
    args = ap.parse_args(argv)

    packages = ["poppler"] + ([] if args.no_ocr else ["tesseract"])
    command = [str(micromamba_path()), "create", "--yes", "--prefix", str(ENV),
               "--channel", "conda-forge", *packages]
    if args.dry_run:
        print(f"would download micromamba ({conda_platform()}) to {micromamba_path()}")
        print("would run: " + " ".join(command))
        print(bin_folder())
        return 0

    fetch_micromamba()
    # Keep micromamba's package cache inside .tools too, not in the home folder.
    env = {**os.environ, "MAMBA_ROOT_PREFIX": str(TOOLS / "root")}
    if ENV.exists():
        command[1] = "install"  # add to the existing env instead of recreating it
    print("running: " + " ".join(command), flush=True)
    if subprocess.run(command, env=env).returncode != 0:
        print("micromamba failed; see the messages above")
        return 1

    folder = bin_folder()
    names = ["pdftotext", "pdftoppm"] + ([] if args.no_ocr else ["tesseract"])
    suffix = ".exe" if os.name == "nt" else ""
    missing = [n for n in names if not (folder / (n + suffix)).exists()]
    if missing:
        print(f"installed, but not found in {folder}: {', '.join(missing)}")
        return 1
    print(f"installed {', '.join(names)} in:")
    print(folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
