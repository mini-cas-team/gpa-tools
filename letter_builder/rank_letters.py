"""python -m letter_builder.rank_letters [--work DIR] [--out FILE] [--config config.yaml]

Asks TypeSafe's Jev to rank each <name>.txt in the work folder (default
<work-folder>/letters, where pdf2txt writes them) as an excellent, good or
normal recommendation letter, one `choice` question per letter, and writes
filename,rank,confidence to --out (default <work>/letter_ranks.csv) once every
letter is ranked.
How Jev is reached (mode cloud or direct, the cloud URL and token variable,
or TypeSafe's model, URL and key variable) comes from config.yaml's
category-builder: jev: section; the question itself is in jev_core/prompts.json.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import jev_core
from jev_category import builder as jev_builder
from jev_category.client import JevError
from letter_builder import RANKS_CSV, parse_cli, pdf2txt

CRITERIA = jev_core.LETTERS["criteria"]  # excellent, good, normal
FIELDS = ["filename", "rank", "confidence"]


def jev_client(raw: dict):
    """The Jev client config.yaml's jev settings ask for (cloud or direct);
    RunError when its token or API key is not set."""
    return jev_builder.client(jev_builder.jev_settings(raw))


def rank(client, letter: str) -> tuple[str, float]:
    """(excellent|good|normal, Jev's confidence)."""
    choice, confidence = client.rank_letter(letter)
    if choice not in CRITERIA:
        raise JevError(f"unexpected answer: {choice}")
    return choice, confidence


def rank_pdfs(client, pdfs: list[Path], work: Path, progress=None):
    """Convert each PDF to <work>/<name>.txt and rank it:
    ([(txt name, rank, confidence)], [{file, error}]).

    ``progress()`` is called after each PDF, ranked or not.
    """
    ranks, failures = [], []
    for pdf in pdfs:
        try:
            txt = pdf2txt.convert_one(pdf, work)
            ranks.append((txt.name, *rank(client, txt.read_text(encoding="utf-8"))))
        except (ValueError, JevError, OSError) as exc:
            failures.append({"file": pdf.name, "error": str(exc)})
        if progress:
            progress()
    return ranks, failures


def read_ranks(path: Path) -> dict[str, dict]:
    """{filename: row} from a ranks CSV; empty when there is none yet."""
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["filename"]: row for row in csv.DictReader(fh) if row.get("filename")}


def write_ranks(path: Path, ranks: list[tuple[str, str, float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        writer.writerows((name, choice, f"{confidence:.3f}") for name, choice, confidence in ranks)


def merge_ranks(path: Path, ranks: list[tuple[str, str, float]]) -> None:
    """Add ``ranks`` to the CSV, replacing rows with the same filename."""
    rows = {name: (name, row["rank"], float(row.get("confidence") or 0))
            for name, row in read_ranks(path).items()}
    rows.update({name: (name, choice, confidence) for name, choice, confidence in ranks})
    write_ranks(path, sorted(rows.values()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help=f"default: <work>/{RANKS_CSV}")
    args, raw, _, folder = parse_cli(parser, argv)
    out = (args.out or folder / RANKS_CSV).expanduser()
    try:
        client = jev_client(raw)
    except RunError as exc:
        raise SystemExit(str(exc))

    ranks = []
    for txt in sorted(folder.glob("*.txt")):
        try:
            choice, confidence = rank(client, txt.read_text(encoding="utf-8"))
        except (JevError, OSError) as exc:
            print(f"failed: {txt.name}: {exc}", file=sys.stderr)
            choice, confidence = "error", 0.0
        print(f"{txt.name}: {choice} ({confidence:.2f})")
        ranks.append((txt.name, choice, confidence))

    write_ranks(out, ranks)
    counts = Counter(choice for _, choice, _ in ranks)
    print(f"wrote {len(ranks)} ranks to {out}: {dict(counts)}")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
