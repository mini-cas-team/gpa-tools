"""Result output: console table, JSON and CSV."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .rank import StudentResult

COLUMNS = [
    ("#", 3),
    ("Student", 18),
    ("Institution", 26),
    ("Program", 22),
    ("GPA", 6),
    ("Raw", 6),
    ("Crs", 4),
    ("Cred", 6),
    ("Gate", 5),
]


def _cell(value, width: int) -> str:
    text = "" if value is None else str(value)
    if len(text) > width:
        text = text[: width - 1] + "…"
    return text.ljust(width)


def console_table(ranked: list[StudentResult], category: str) -> str:
    lines = [
        f"Ranking by average {category} GPA (rescaled so A = 4.00 everywhere, "
        f"then capped at 4.00)",
        "",
        "".join(_cell(name, width) + " " for name, width in COLUMNS).rstrip(),
        "".join("-" * width + " " for _, width in COLUMNS).rstrip(),
    ]
    for position, r in enumerate(ranked, start=1):
        row = [
            position,
            r.student,
            r.institution,
            r.program,
            f"{r.gpa_capped:.3f}" if r.gpa_capped is not None else "-",
            f"{r.gpa_raw:.3f}" if r.gpa_raw is not None else "-",
            r.category_courses,
            f"{r.category_credits:g}",
            r.gate,
        ]
        lines.append("".join(_cell(v, w) + " " for v, (_, w) in zip(row, COLUMNS)).rstrip())
    return "\n".join(lines)


def write_outputs(
    out_dir: Path,
    category: str,
    ranked: list[StudentResult],
    held: list[tuple[StudentResult, str]],
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    courses_dir = out_dir / "extracted"
    courses_dir.mkdir(exist_ok=True)
    slug = category.lower()

    payload = {
        "category": category,
        "ranked": [_summary_dict(r, position) for position, r in enumerate(ranked, start=1)],
        "held_back": [
            {"student": r.student, "file": r.file, "reason": reason, "warnings": r.warnings}
            for r, reason in held
        ],
    }
    json_path = out_dir / f"rank_{slug}_gpa.json"
    json_path.write_text(json.dumps(payload, indent=2))

    csv_path = out_dir / f"rank_{slug}_gpa.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "rank", "student", "institution", "program",
                f"{category}_GPA", "all_courses_GPA",
                f"{slug}_courses", "total_courses", f"{slug}_all_pct",
                "gate", "file",
            ]
        )
        for position, r in enumerate(ranked, start=1):
            writer.writerow(
                [
                    position, r.student, r.institution, r.program,
                    r.gpa_capped, r.overall_gpa_capped,
                    r.category_courses, r.total_courses, r.category_course_pct,
                    r.gate, r.file,
                ]
            )

    # Namespaced by category: several categories are ranked in one run and each
    # writes its own view of the same student, so a bare name would collide.
    for result in [r for r, _ in held] + ranked:
        detail = asdict(result)
        (courses_dir / f"{Path(result.file).stem}.{slug}.courses.json").write_text(
            json.dumps(detail, indent=2)
        )

    return {"json": json_path, "csv": csv_path, "courses": courses_dir}


def _summary_dict(result: StudentResult, position: int) -> dict:
    data = asdict(result)
    data.pop("courses", None)
    data["rank"] = position
    return data
