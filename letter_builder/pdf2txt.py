"""python -m letter_builder.pdf2txt [--folder DIR] [--work DIR] [--config config.yaml]

Converts every <name>.pdf in the folder (default <input-folder>/letters) to
<name>.txt in the work folder (default <work-folder>/letters), with
`pdftotext -layout` (upload's own pdf_to_txt).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from letter_builder import parse_cli
from upload.convert import pdf_to_txt


def convert_one(pdf: Path, work: Path) -> Path:
    """<work>/<name>.txt for ``pdf``; ValueError when pdftotext fails or finds no text."""
    work.mkdir(parents=True, exist_ok=True)
    txt = work / f"{pdf.stem}.txt"
    pdf_to_txt(pdf, txt)
    return txt


def convert(folder: Path, work: Path) -> tuple[int, list[str]]:
    """(PDFs in ``folder`` converted into ``work``, names that failed)."""
    done, failed = 0, []
    for pdf in sorted(folder.glob("*.pdf")):
        try:
            convert_one(pdf, work)
        except (OSError, ValueError) as exc:
            failed.append(pdf.name)
            print(f"failed: {pdf.name}: {exc}", file=sys.stderr)
        else:
            done += 1
    return done, failed


def main(argv=None) -> int:
    _, _, folder, work = parse_cli(argparse.ArgumentParser(description=__doc__.splitlines()[0]), argv)
    done, failed = convert(folder, work)
    print(f"converted {done} PDFs in {folder} into {work}, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
