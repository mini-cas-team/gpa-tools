"""Turn transcript PDFs into common JSON with per-course GPA.

    python3 -m upload [--config config.yaml]

For each ``*.pdf`` in config.yaml's ``pdf-folder``:
  1. ``pdftotext -layout``       -> <work-folder>/<name>.txt
  2. common/txt2common.convert  -> <work-folder>/<name>.common.json
  3. common/add_gpa.add_gpa     -> same file, with gpa and gpa_4 per course
The run is logged to <log-folder>/upload.log.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from .convert import add_tool_folders, pdf_to_common_json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("upload")


def setup_logging(log_folder: Path) -> Path:
    log_folder.mkdir(parents=True, exist_ok=True)
    path = log_folder / "upload.log"
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(file_handler)
    log.addHandler(logging.StreamHandler(sys.stdout))
    log.setLevel(logging.INFO)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m upload", description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    add_tool_folders(cfg.get("tool-folders"))
    pdf_folder = Path(cfg["pdf-folder"]).expanduser()
    work = Path(cfg["work-folder"]).expanduser()
    log_path = setup_logging(Path(cfg.get("log-folder") or "log").expanduser())
    work.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(pdf_folder.glob("*.pdf"))
    log.info("Upload run: %d PDF(s) from %s into %s", len(pdfs), pdf_folder, work)
    failed = 0
    for pdf in pdfs:
        try:
            warnings = pdf_to_common_json(pdf, work)["warnings"]
        except (OSError, ValueError) as exc:
            failed += 1
            log.error("%s: %s", pdf.name, exc)
            continue
        for w in warnings:
            log.warning("%s: %s", pdf.name, w)
    log.info("Done: %d converted, %d failed. Log: %s", len(pdfs) - failed, failed, log_path)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
