"""PDF -> txt -> common JSON, shared by ``python3 -m upload`` and browser_view."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .common.add_gpa import add_gpa
from .common.txt2common import convert


def add_tool_folders(folders) -> None:
    """Put config.yaml's ``tool-folders`` first on PATH.

    The installer (install/) puts pdftotext in a folder of the user's own when
    there is no admin access, and records it here.
    """
    if isinstance(folders, str):
        folders = [folders]
    current = os.environ.get("PATH", "").split(os.pathsep)
    extra = [str(Path(str(f)).expanduser()) for f in folders or []]
    extra = [f for f in extra if f not in current]
    if extra:
        os.environ["PATH"] = os.pathsep.join(extra + current)


def pdf_to_txt(pdf: Path, txt: Path) -> None:
    """``pdftotext -layout``; raises ValueError for a PDF with no text layer."""
    # The full path, which also finds pdftotext.exe on Windows.
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        raise ValueError("pdftotext not found on PATH or in config.yaml's tool-folders")
    try:
        subprocess.run([pdftotext, "-layout", "-enc", "UTF-8", str(pdf), str(txt)], check=True,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    except subprocess.CalledProcessError as exc:
        raise ValueError(exc.stderr.strip() or f"pdftotext failed ({exc.returncode})") from None
    if not txt.read_text(encoding="utf-8", errors="replace").strip():
        raise ValueError("no text layer (scanned PDF?); OCR is not supported here")


def pdf_to_common_json(pdf: Path, work: Path) -> dict:
    """Write <work>/<name>.txt and <work>/<name>.common.json (with gpa, gpa_4)."""
    txt = work / f"{pdf.stem}.txt"
    pdf_to_txt(pdf, txt)
    doc = add_gpa(convert(txt))
    (work / f"{pdf.stem}.common.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return doc
