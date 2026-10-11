"""python -m llm_category --category STEM [--config config.yaml] [--limit N] [--publish]

The llm builder on its own; same as python -m category_builder --builder llm.
"""

from __future__ import annotations

import sys

from category_builder.__main__ import main as builder_main


def main(argv=None) -> int:
    return builder_main(["--builder", "llm", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    sys.exit(main())
