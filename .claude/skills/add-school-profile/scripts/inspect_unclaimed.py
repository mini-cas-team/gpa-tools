"""Reconnaissance for adding a layout profile: what does gpa-ana see in a PDF?

No arguments scans the configured pdf-folder and reports only files that no
profile claims.  Explicit paths inspect those files whether claimed or not.

    .venv312/bin/python .claude/skills/add-school-profile/scripts/inspect_unclaimed.py
    .venv312/bin/python .claude/skills/add-school-profile/scripts/inspect_unclaimed.py ~/Templates/transcripts/new.pdf

Prints the four things the share-or-split decision turns on -- header bands,
grade scale, code grammar and department vocabulary -- plus, for unclaimed
files, whether an existing profile family already matches all four.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

# Run from the project root; the script lives four directories down from it, so
# put the root on the path before importing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from gpa_ana.config import Config
from gpa_ana.extract import clean_lines, extract_pages
from gpa_ana.layout import Profile, dispatch, load_profiles
from gpa_ana.layout.handlers.term_block_table import HEADER_LINE, parse_header
from gpa_ana.pipeline import MIN_DISPATCH_SCORE, parse_transcript

GRAMMARS = ["space_suffix", "mit_dotted", "jhu_dotted", "alpha_prefix"]


def fingerprint_of(path: Path, profiles: list[Profile]) -> dict:
    pages = extract_pages(path)
    lines = [ln for p in pages for ln in clean_lines(p.text)]
    profile, score = dispatch("\n".join(lines), profiles)
    transcript = parse_transcript(path, profiles)
    bands = {
        tuple(c["label"] for c in parse_header(ln))
        for ln in lines
        if HEADER_LINE.match(ln.strip())
    }
    return {
        "path": path,
        "name": next((ln.strip() for ln in lines if ln.strip()), "?"),
        "profile": profile,
        "score": score,
        "claimed": score >= MIN_DISPATCH_SCORE,
        "bands": bands,
        "scale": (
            (transcript.scale.anchor, transcript.scale.source)
            if transcript.scale
            else None
        ),
        "codes": sorted({c.code for c in transcript.courses if c.code}),
        "depts": sorted({c.dept for c in transcript.courses if c.dept}),
    }


def report(info: dict, families: dict[str, dict]) -> None:
    print("=" * 78)
    print(f"### {info['path'].name}   printed name: {info['name']!r}")
    flag = "" if info["claimed"] else "   <-- UNCLAIMED (generic fallback)"
    print(f"    dispatch : {info['profile'].id} score {info['score']} "
          f"(needs >= {MIN_DISPATCH_SCORE}){flag}")
    print(f"    bands    : {' | '.join(str(b) for b in info['bands']) or '(none found)'}")
    print(f"    scale    : {info['scale']}")
    print(f"    depts    : {', '.join(info['depts']) or '(none)'}")

    if not info["codes"]:
        print("\n    !! no course rows parsed -- the page shape may need a new *handler*,")
        print("       not just a profile.  Compare with term_block_table's docstring.")
        return

    sample = info["codes"][:8]
    print(f"    codes    : {', '.join(sample)}")
    print("\n    -- department token each grammar would produce --")
    for grammar in GRAMMARS:
        probe = Profile(id="probe", institution="probe", code_grammar=grammar)
        try:
            tokens = [probe.dept_of(c) for c in sample]
        except Exception as exc:
            print(f"       {grammar:<14} !! {exc}")
            continue
        print(f"       {grammar:<14} {', '.join(str(t) for t in tokens)}")

    # Does an existing profile already serve a family this file could join?
    for pid, fam in sorted(families.items()):
        if pid == info["profile"].id:
            continue
        same_bands = info["bands"] == fam["bands"]
        same_scale = info["scale"] == fam["scale"]
        same_vocab = set(info["depts"]) <= fam["depts"]
        if same_bands and same_scale and same_vocab:
            print(f"\n    >> MATCHES the '{pid}' family on bands, scale and vocabulary.")
            print(f"       Prefer adding {info['name']!r} to that profile's fingerprint")
            print(f"       over writing a new file -- but confirm the shared department")
            print(f"       tokens still MEAN the same thing here before you do.")


def main(argv: list[str]) -> int:
    profiles = load_profiles()
    if argv:
        targets, only_unclaimed = [Path(a).expanduser() for a in argv], False
    else:
        targets, only_unclaimed = Config.load(Path("config.yaml")).pdfs(), True

    scanned = [fingerprint_of(p, profiles) for p in targets]

    families: dict[str, dict] = defaultdict(
        lambda: {"bands": set(), "scale": None, "depts": set()}
    )
    for info in scanned:
        if not info["claimed"]:
            continue
        fam = families[info["profile"].id]
        fam["bands"] = info["bands"]
        fam["scale"] = info["scale"]
        fam["depts"] |= set(info["depts"])

    shown = 0
    for info in scanned:
        if only_unclaimed and info["claimed"]:
            continue
        report(info, families)
        shown += 1

    print("=" * 78)
    if only_unclaimed and not shown:
        print("Every PDF is claimed by a profile. Nothing to add.")
    else:
        print(f"{shown} file(s) reported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
