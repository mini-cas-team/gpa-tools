"""python -m letter_builder.rank_letters [--folder DIR] [--out FILE] [--config config.yaml]

Asks TypeSafe's Jev to rank each <name>.txt in the folder (default:
config.yaml's letters-folder) as an excellent, good or normal recommendation
letter, one `choice` question per letter, and writes filename,rank,confidence
to --out (default <folder>/letter_ranks.csv) once every letter is ranked.
Model, API URL and the API key's environment variable come from config.yaml's
category-builder: jev: section.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import category_builder
from category_builder.common import RunError
from jev_category.builder import DEFAULTS, api_key
from jev_category.client import JevClient, JevError
from letter_builder import RANKS_CSV, parse_cli, pdf2txt

INSTRUCTIONS = "How strong a recommendation is this letter for the student it is about?"
CRITERIA = {
    "excellent": "The writer gives an exceptional, unreserved endorsement backed by specific evidence: "
                 "ranks the student among the very best they have known, with concrete achievements.",
    "good": "A clearly positive, supportive endorsement with some specific evidence, "
            "but without placing the student at the very top.",
    "normal": "A routine or lukewarm letter: generic praise, little specific evidence, "
              "or confirms facts more than it recommends.",
}
FIELDS = ["filename", "rank", "confidence"]


def jev_client(raw: dict) -> JevClient:
    """A client from config.yaml's jev settings; RunError when the API key is not set."""
    jev_cfg = {**DEFAULTS, **category_builder.settings(raw, "jev")}
    return JevClient(jev_cfg["api-url"], jev_cfg["model"], api_key({"key_env": jev_cfg["api-key-env"]}))


def rank(client: JevClient, letter: str) -> tuple[str, float]:
    """(excellent|good|normal, Jev's confidence)."""
    question = {"type": "choice", "instructions": INSTRUCTIONS, "criteria": CRITERIA}
    answer = client.answers({"letter": letter}, {"rank": question}).get("rank") or {}
    if answer.get("choice") not in CRITERIA:
        raise JevError(f"unexpected answer: {answer}")
    return answer["choice"], float(answer.get("confidence") or 0)


def rank_pdfs(client: JevClient, pdfs: list[Path], progress=None):
    """Convert and rank each PDF: ([(txt name, rank, confidence)], [{file, error}]).

    ``progress()`` is called after each PDF, ranked or not.
    """
    ranks, failures = [], []
    for pdf in pdfs:
        try:
            txt = pdf2txt.convert_one(pdf)
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
    parser.add_argument("--out", type=Path, help=f"default: <folder>/{RANKS_CSV}")
    args, raw, folder = parse_cli(parser, argv)
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
