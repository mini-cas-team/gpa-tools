"""PDF -> txt -> common JSON, shared by ``python3 -m upload`` and browser_view."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .common.add_gpa import add_gpa
from .common.txt2common import convert


def pdf_to_txt(pdf: Path, txt: Path) -> None:
    """``pdftotext -layout``; raises ValueError for a PDF with no text layer."""
    try:
        subprocess.run(["pdftotext", "-layout", str(pdf), str(txt)], check=True,
                       capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise ValueError(exc.stderr.strip() or f"pdftotext failed ({exc.returncode})") from None
    if not txt.read_text().strip():
        raise ValueError("no text layer (scanned PDF?); OCR is not supported here")


def pdf_to_common_json(pdf: Path, work: Path) -> dict:
    """Write <work>/<name>.txt and <work>/<name>.common.json (with gpa, gpa_4)."""
    txt = work / f"{pdf.stem}.txt"
    pdf_to_txt(pdf, txt)
    doc = add_gpa(convert(txt))
    (work / f"{pdf.stem}.common.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    )
    return doc
