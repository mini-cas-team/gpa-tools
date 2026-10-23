"""python -m letter_builder.pdf2txt [--folder DIR] [--config config.yaml]

Converts every <name>.pdf in the folder (default: config.yaml's letters-folder)
to <name>.txt next to it, with `pdftotext -layout` (the same tool upload uses).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from letter_builder import letters_folder, load_raw


def convert_one(pdf: Path) -> Path:
    """<name>.txt beside ``pdf``; RuntimeError when pdftotext fails."""
    txt = pdf.with_suffix(".txt")
    try:
        result = subprocess.run(["pdftotext", "-layout", str(pdf), str(txt)],
                                capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError(f"pdftotext: {exc}") from None
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"pdftotext exited with {result.returncode}")
    return txt


def convert(folder: Path) -> tuple[int, list[str]]:
    """(PDFs converted, names that failed)."""
    done, failed = 0, []
    for pdf in sorted(folder.glob("*.pdf")):
        try:
            convert_one(pdf)
        except RuntimeError as exc:
            failed.append(pdf.name)
            print(f"failed: {pdf.name}: {exc}", file=sys.stderr)
        else:
            done += 1
    return done, failed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args(argv)
    folder = (args.folder or letters_folder(load_raw(args.config))).expanduser()
    done, failed = convert(folder)
    print(f"converted {done} PDFs in {folder}, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
