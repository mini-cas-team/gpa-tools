"""One analysis run, shared by every front end.

The CLI and the web UI differ only in how they collect settings and how they
render the answer.  The work between those two points -- parse each PDF once,
tag it with every requested ruleset, rank per category, write the files -- is
the same, so it lives here rather than in either front end.  A second caller
was what forced the split: duplicating the orchestration is how the two would
quietly drift apart.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .categories import Ruleset
from .layout import Profile
from .model import Transcript
from .pipeline import apply_categories, parse_transcript
from .rank import StudentResult, rank
from .rank import summarise
from .report import write_outputs


@dataclass
class CategoryRanking:
    """One category's view of the run."""

    category: str
    ranked: list[StudentResult] = field(default_factory=list)
    held: list[tuple[StudentResult, str]] = field(default_factory=list)
    outputs: dict[str, Path] | None = None  # None when nothing was written


@dataclass
class AnalysisRun:
    """Everything one run produced, for a front end to render however it likes."""

    categories: list[CategoryRanking] = field(default_factory=list)
    transcripts: list[Transcript] = field(default_factory=list)
    # (transcript, {category: result}) in input order -- the per-file detail view.
    parsed: list[tuple[Transcript, dict[str, StudentResult]]] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def gates(self) -> dict[str, int]:
        """Gate tally. Describes the parse, so it is counted once per run."""
        counts = {"pass": 0, "FAIL": 0, "none": 0}
        for transcript in self.transcripts:
            counts[transcript.gate] = counts.get(transcript.gate, 0) + 1
        return counts

    @property
    def warnings(self) -> list[tuple[str, str]]:
        """(file, warning) pairs, flattened in input order."""
        return [(t.file, w) for t in self.transcripts for w in t.warnings]


def analyse(
    pdfs: list[Path],
    rulesets: list[Ruleset],
    profiles: list[Profile],
    *,
    force_ocr: bool = False,
    dpi: int = 300,
    workers: int = 1,
    min_category_courses: int = 0,
    out_folder: Path | None = None,
    on_error: Callable[[Path, Exception], None] | None = None,
) -> AnalysisRun:
    """Parse every PDF once, rank it under every ruleset, optionally write files.

    ``out_folder`` of None ranks without writing, which is what a front end
    wants when it is only going to display the answer.
    """
    run = AnalysisRun()

    def parse_one(pdf: Path):
        transcript = parse_transcript(
            pdf, profiles, force_ocr=force_ocr, dpi=dpi, workers=workers
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

    if len(pdfs) > 1 and workers > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(pdfs))) as pool:
            outcomes = list(pool.map(attempt, pdfs))
    else:
        outcomes = [attempt(pdf) for pdf in pdfs]

    by_category: dict[str, list[StudentResult]] = {r.id: [] for r in rulesets}
    for pdf, parsed, error in outcomes:
        if error is not None:
            run.failures.append((pdf.name, str(error)))
            if on_error is not None:
                on_error(pdf, error)
            continue
        transcript, results = parsed
        run.transcripts.append(transcript)
        run.parsed.append((transcript, results))
        for category_id, result in results.items():
            by_category[category_id].append(result)

    for ruleset in rulesets:
        ranked, held = rank(by_category[ruleset.id], min_courses=min_category_courses)
        outcome = CategoryRanking(category=ruleset.id, ranked=ranked, held=held)
        if out_folder is not None:
            outcome.outputs = write_outputs(out_folder, ruleset.id, ranked, held)
        run.categories.append(outcome)

    return run
