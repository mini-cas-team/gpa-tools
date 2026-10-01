"""Command line entry point: ``python -m gpa_ana``.

Settings in, console and files out.  The run itself is ``service.analyse``,
shared with the web UI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .categories import load_ruleset
from .config import Config, ConfigError, _categories
from .extract import configure_ocr_concurrency
from .layout import load_profiles
from .report import console_table
from .service import analyse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpa-ana",
        description="Rank students by average category GPA from transcript PDFs.",
    )
    parser.add_argument("-c", "--config", type=Path, default=Path("config.yaml"))
    parser.add_argument(
        "--category",
        help="override the category list in config.yaml (comma-separated for several)",
    )
    parser.add_argument("--pdf-folder", type=Path, help="override pdf-folder in config.yaml")
    parser.add_argument("--force-ocr", action="store_true", help="ignore any embedded text layer")
    parser.add_argument("-j", "--jobs", type=int, help="parallel workers (default: one per CPU)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show per-file detail")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.category:
        try:
            config.categories = _categories(args.category.split(","), args.config)
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 2
    if args.pdf_folder:
        config.pdf_folder = args.pdf_folder
    if args.force_ocr:
        config.force_ocr = True
    if args.jobs:
        config.jobs = args.jobs

    try:
        pdfs = config.pdfs()
        rulesets = [load_ruleset(name) for name in config.categories]
        profiles = load_profiles()
    except Exception as exc:  # config/ruleset/profile problems are user-fixable
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"gpa-ana: {len(pdfs)} PDF(s) in {config.pdf_folder}, "
        f"categor{'y' if len(rulesets) == 1 else 'ies'} "
        f"{', '.join(r.id for r in rulesets)}, {len(profiles)} layout profile(s)\n"
    )

    # Work is subprocess-bound (pdftotext, pdftoppm, tesseract), so threads get
    # real parallelism. Files and pages are both parallelised; a semaphore in
    # the extractor caps concurrent OCR processes, so the two pools cannot
    # multiply into an oversubscribed machine. This matters when one slow
    # scanned file sits among many fast ones -- it still gets the whole box.
    workers = config.worker_count
    configure_ocr_concurrency(workers)

    run = analyse(
        pdfs,
        rulesets,
        profiles,
        force_ocr=config.force_ocr,
        dpi=config.ocr_dpi,
        workers=workers,
        min_category_courses=config.min_category_courses,
        out_folder=config.out_folder,
        on_error=lambda pdf, exc: print(f"  !! {pdf.name}: {exc}", file=sys.stderr),
    )

    if args.verbose:
        for transcript, results in run.parsed:
            for result in results.values():
                _print_detail(result, transcript)

    if not run.transcripts:
        print("no transcripts could be parsed", file=sys.stderr)
        return 1

    for outcome in run.categories:
        print(console_table(outcome.ranked, outcome.category))

        if outcome.held:
            print("\nHeld back (not ranked):")
            for result, reason in outcome.held:
                print(f"  - {result.student} ({result.file}): {reason}")
        print()

    # Gates and warnings describe the parse, not the taxonomy, so they are
    # reported once however many categories were ranked.
    gates = run.gates
    print(
        f"Validation gates: {gates['pass']} pass, {gates['FAIL']} fail, "
        f"{gates['none']} with nothing to check against"
    )

    if run.warnings:
        print("\nWarnings:")
        for file, warning in run.warnings:
            print(f"  - {file}: {warning}")

    if run.failures:
        print(f"\n{len(run.failures)} file(s) could not be parsed:")
        for name, message in run.failures:
            print(f"  - {name}: {message}")

    print()
    for outcome in run.categories:
        if outcome.outputs:
            print(f"Wrote {outcome.outputs['json']}, {outcome.outputs['csv']}")
    written = [o.outputs for o in run.categories if o.outputs]
    if written:
        print(f"Per-student detail in {written[0]['courses']}/")
    return 0


def _print_detail(result, transcript) -> None:
    print(f"  {result.file}: {result.institution} -- {len(transcript.courses)} courses, "
          f"{result.category_courses} {result.category}, gate={result.gate}")
    for check in transcript.checks:
        print(f"      [{check.status}] {check.name}: {check.detail}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
