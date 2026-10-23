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
import os
import sys
from pathlib import Path

from jev_category.builder import DEFAULTS
from jev_category.client import JevClient, JevError
from letter_builder import RANKS_CSV, letters_folder, load_raw

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
    """A client from config.yaml's jev settings; JevError when the API key is not set."""
    jev_cfg = {**DEFAULTS, **((raw.get("category-builder") or {}).get("jev") or {})}
    api_key = os.environ.get(jev_cfg["api-key-env"])
    if not api_key:
        raise JevError(f"set the API key in the {jev_cfg['api-key-env']} environment variable")
    return JevClient(jev_cfg["api-url"], jev_cfg["model"], api_key)


def rank(client: JevClient, letter: str) -> tuple[str, float]:
    """(excellent|good|normal, Jev's confidence)."""
    body = {"model": client.model, "state": {"letter": letter},
            "questions": {"rank": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": CRITERIA}}}
    answer = (client._post(body).get("answers") or {}).get("rank") or {}
    if answer.get("choice") not in CRITERIA:
        raise JevError(f"unexpected answer: {answer}")
    return answer["choice"], float(answer.get("confidence") or 0)


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
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--out", type=Path, help=f"default: <folder>/{RANKS_CSV}")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args(argv)
    raw = load_raw(args.config)
    folder = (args.folder or letters_folder(raw)).expanduser()
    out = (args.out or folder / RANKS_CSV).expanduser()
    try:
        client = jev_client(raw)
    except JevError as exc:
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
    counts = {c: sum(1 for _, r, _ in ranks if r == c) for c in (*CRITERIA, "error")}
    print(f"wrote {len(ranks)} ranks to {out}: {counts}")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
