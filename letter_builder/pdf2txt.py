"""python -m letter_builder.pdf2txt [--folder DIR] [--config config.yaml]

Converts every <name>.pdf in the folder (default: config.yaml's letters-folder)
to <name>.txt next to it, with `pdftotext -layout` (upload's own pdf_to_txt).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from letter_builder import parse_cli
from upload.convert import pdf_to_txt


def convert_one(pdf: Path) -> Path:
    """<name>.txt beside ``pdf``; ValueError when pdftotext fails or finds no text."""
    txt = pdf.with_suffix(".txt")
    pdf_to_txt(pdf, txt)
    return txt


def convert(folder: Path) -> tuple[int, list[str]]:
    """(PDFs converted, names that failed)."""
    done, failed = 0, []
    for pdf in sorted(folder.glob("*.pdf")):
        try:
            convert_one(pdf)
        except (OSError, ValueError) as exc:
            failed.append(pdf.name)
            print(f"failed: {pdf.name}: {exc}", file=sys.stderr)
        else:
            done += 1
    return done, failed


def main(argv=None) -> int:
    _, _, folder = parse_cli(argparse.ArgumentParser(description=__doc__.splitlines()[0]), argv)
    done, failed = convert(folder)
    print(f"converted {done} PDFs in {folder}, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
