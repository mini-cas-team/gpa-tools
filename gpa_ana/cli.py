"""Command line entry point: ``python -m gpa_ana``."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .categories import load_ruleset
from .config import Config, ConfigError, _categories
from .extract import configure_ocr_concurrency
from .layout import load_profiles
from .pipeline import apply_categories, parse_transcript
from .rank import rank, summarise
from .report import console_table, write_outputs


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
    parallel_files = len(pdfs) > 1 and workers > 1

    def parse_one(pdf: Path):
        transcript = parse_transcript(
            pdf,
            profiles,
            force_ocr=config.force_ocr,
            dpi=config.ocr_dpi,
            workers=workers,
        )
        # One parse feeds every category: tagging is a set, so the rulesets
        # accumulate onto the same courses rather than competing.
        for ruleset in rulesets:
            apply_categories(transcript, ruleset)
        return transcript, {r.id: summarise(transcript, r.id) for r in rulesets}

    def attempt(pdf: Path):
        try:
            return pdf, parse_one(pdf), None
        except Exception as exc:  # one bad file must not sink the batch
            return pdf, None, exc

    if parallel_files:
        with ThreadPoolExecutor(max_workers=min(workers, len(pdfs))) as pool:
            outcomes = list(pool.map(attempt, pdfs))
    else:
        outcomes = [attempt(pdf) for pdf in pdfs]

    transcripts = []
    by_category: dict[str, list] = {r.id: [] for r in rulesets}
    failures = []
    for pdf, parsed, error in outcomes:
        if error is not None:
            failures.append((pdf.name, str(error)))
            print(f"  !! {pdf.name}: {error}", file=sys.stderr)
            continue
        transcript, results = parsed
        transcripts.append(transcript)
        for category_id, result in results.items():
            by_category[category_id].append(result)
        if args.verbose:
            for result in results.values():
                _print_detail(result, transcript)

    if not transcripts:
        print("no transcripts could be parsed", file=sys.stderr)
        return 1

    written = []
    for ruleset in rulesets:
        ranked, held = rank(
            by_category[ruleset.id], min_courses=config.min_category_courses
        )
        print(console_table(ranked, ruleset.id))

        if held:
            print("\nHeld back (not ranked):")
            for result, reason in held:
                print(f"  - {result.student} ({result.file}): {reason}")
        print()
        written.append(write_outputs(config.out_folder, ruleset.id, ranked, held))

    # Gates and warnings describe the parse, not the taxonomy, so they are
    # reported once however many categories were ranked.
    gates = {"pass": 0, "FAIL": 0, "none": 0}
    for transcript in transcripts:
        gates[transcript.gate] = gates.get(transcript.gate, 0) + 1
    print(
        f"Validation gates: {gates['pass']} pass, {gates['FAIL']} fail, "
        f"{gates['none']} with nothing to check against"
    )

    flagged = [t for t in transcripts if t.warnings]
    if flagged:
        print("\nWarnings:")
        for transcript in flagged:
            for warning in transcript.warnings:
                print(f"  - {transcript.file}: {warning}")

    if failures:
        print(f"\n{len(failures)} file(s) could not be parsed:")
        for name, message in failures:
            print(f"  - {name}: {message}")

    print()
    for paths in written:
        print(f"Wrote {paths['json']}, {paths['csv']}")
    print(f"Per-student detail in {written[0]['courses']}/")
    return 0


def _print_detail(result, transcript) -> None:
    print(f"  {result.file}: {result.institution} -- {len(transcript.courses)} courses, "
          f"{result.category_courses} {result.category}, gate={result.gate}")
    for check in transcript.checks:
        print(f"      [{check.status}] {check.name}: {check.detail}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
