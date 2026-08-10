"""PDF -> page text, whichever path the file needs.

Two paths produce the *same* shape (a list of column-aligned text pages), so
layout handlers never learn whether a file was born digital or scanned:

    native text layer  ->  pdftotext -layout
    image-only page    ->  render, split into regions, OCR, rebuild spacing

The OCR path deliberately detects regions *before* reading, then runs
tesseract --psm 6 within each region.  Running psm 6 across a whole page welds
neighbouring blocks (a mailing address onto course rows) into single lines.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

# A page with fewer than this many characters of embedded text is treated as an
# image, even if the PDF technically carries a text layer (scan + stray label).
MIN_TEXT_CHARS = 200

# A page fragment narrower than this is a table column, not an independent
# block; it is merged into its neighbour instead of being read on its own.
MIN_REGION_FRACTION = 0.25

# Tesseract parallelises internally with OpenMP, and on transcript-sized pages
# that is actively harmful: the same page measured 2.4s with default threads
# and 20.7s at a different resolution, varying run to run. Pinning it to one
# thread makes each page consistently fast and leaves the cores free for
# page-level parallelism here, which scales far better.
TESSERACT_ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}

# OCR concurrency is bounded here rather than at any one call site, so files and
# pages can both be parallelised without the two pools multiplying into an
# oversubscribed machine. Threads waiting on this are cheap; tesseract
# processes are not.
_OCR_SLOTS = threading.BoundedSemaphore(os.cpu_count() or 1)


def configure_ocr_concurrency(limit: int) -> None:
    """Set the maximum number of tesseract processes running at once."""
    global _OCR_SLOTS
    _OCR_SLOTS = threading.BoundedSemaphore(max(1, limit))


@dataclass
class Page:
    number: int
    text: str
    source: str = "text"  # "text" | "ocr"
    confidence: float | None = None
    regions: int = 1


class ExtractionError(RuntimeError):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise ExtractionError(f"required external tool not found on PATH: {tool}")
    return path


def extract_pages(
    pdf: Path, *, dpi: int = 300, force_ocr: bool = False, workers: int = 1
) -> list[Page]:
    """Return one Page per PDF page, routing each page independently."""
    pages = [] if force_ocr else _text_layer_pages(pdf)
    if not pages:
        return _ocr_pages(pdf, dpi=dpi, workers=workers)

    scanned = [p.number for p in pages if len(p.text.strip()) < MIN_TEXT_CHARS]
    if not scanned:
        return pages

    # One render pass for every scanned page, rather than one per page.
    replacements = {
        p.number: p for p in _ocr_pages(pdf, dpi=dpi, pages=scanned, workers=workers)
    }
    return [replacements.get(p.number, p) for p in pages]


# --------------------------------------------------------------------------
# native text layer
# --------------------------------------------------------------------------


def _text_layer_pages(pdf: Path) -> list[Page]:
    _require("pdftotext")
    proc = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    # pdftotext separates pages with a form feed.
    chunks = proc.stdout.split("\f")
    if chunks and not chunks[-1].strip():
        chunks.pop()
    return [Page(number=i + 1, text=t, source="text") for i, t in enumerate(chunks)]


# --------------------------------------------------------------------------
# OCR path
# --------------------------------------------------------------------------


def _ocr_pages(
    pdf: Path, *, dpi: int = 300, pages: list[int] | None = None, workers: int = 1
) -> list[Page]:
    _require("pdftoppm")
    _require("tesseract")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        cmd = ["pdftoppm", "-gray", "-r", str(dpi), "-png"]
        if pages:
            cmd += ["-f", str(min(pages)), "-l", str(max(pages))]
        cmd += [str(pdf), str(tmpdir / "page")]
        subprocess.run(cmd, check=True, capture_output=True)

        # pdftoppm names each file after its real page number, so the mapping
        # survives a rendered range that starts anywhere.
        wanted = set(pages) if pages else None
        images = [
            (number, img)
            for img in sorted(tmpdir.glob("page-*.png"))
            if (number := _page_number(img)) is not None
            and (wanted is None or number in wanted)
        ]

        if workers > 1 and len(images) > 1:
            with ThreadPoolExecutor(max_workers=min(workers, len(images))) as pool:
                results = list(pool.map(lambda pair: _ocr_image(pair[1]), images))
        else:
            results = [_ocr_image(img) for _, img in images]

        return [
            Page(number=number, text=text, source="ocr", confidence=conf, regions=nregions)
            for (number, _), (text, conf, nregions) in zip(images, results)
        ]


def _page_number(img: Path) -> int | None:
    tail = img.stem.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _ocr_image(img: Path) -> tuple[str, float | None, int]:
    """OCR one page image: region split for recognition, global rebuild for layout.

    Each region is cropped and read separately so ``--psm 6`` applies within a
    uniform block, which is what keeps tesseract from welding neighbouring
    blocks into single lines.  Every word is then translated back into
    whole-page coordinates before the text is rebuilt.

    Reading per region but rebuilding globally matters: a wide gap between a
    table's Title and Grade columns looks exactly like the gutter between two
    side-by-side tables.  Emitting the regions as separate blocks would tear
    grades and credits off their own course rows.  Placing every word at its
    true page position lets a single table reassemble, whatever the split
    guessed.
    """
    boxes = _detect_regions(img)
    words: list[dict] = []
    confs: list[float] = []

    for box in boxes:
        left, top, _, _ = box
        region_words, conf = _tesseract_words(img, box)
        if conf is not None:
            confs.append(conf)
        for word in region_words:
            word["left"] += left
            word["top"] += top
            words.append(word)

    mean_conf = sum(confs) / len(confs) if confs else None
    return _words_to_layout_text(words), mean_conf, len(boxes)


def _detect_regions(img: Path) -> list[tuple[int, int, int, int]]:
    """Split a page into column blocks at full-height whitespace gutters.

    Returns (left, top, width, height) boxes.  Falls back to a single
    whole-page box when numpy/Pillow are unavailable.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:  # optional dependency -- degrade, don't crash
        return [(0, 0, 0, 0)]

    with Image.open(img) as im:
        arr = np.asarray(im.convert("L"))
    height, width = arr.shape
    ink = (arr < 200).sum(axis=0)  # ink per pixel column

    # A gutter is a run of near-empty columns wide enough not to be a word gap.
    min_gutter = max(int(width * 0.02), 12)
    empty = ink <= max(1, int(height * 0.002))

    runs: list[tuple[int, int]] = []
    start = None
    for x, is_empty in enumerate(empty):
        if is_empty and start is None:
            start = x
        elif not is_empty and start is not None:
            if x - start >= min_gutter:
                runs.append((start, x))
            start = None
    if start is not None and width - start >= min_gutter:
        runs.append((start, width))

    # Interior gutters only -- leading/trailing whitespace is a margin.
    interior = [(a, b) for a, b in runs if a > width * 0.08 and b < width * 0.92]
    if not interior:
        return [(0, 0, 0, 0)]

    # The regions must *partition* the page. Discarding a narrow segment would
    # discard the words inside it -- a table's Grade and Credits columns are
    # each narrower than any sensible minimum -- so slivers are merged into
    # their neighbour rather than dropped.
    cuts = [0] + [(a + b) // 2 for a, b in interior] + [width]
    segments: list[list[int]] = []
    for left, right in zip(cuts, cuts[1:]):
        if segments and (right - left) < width * MIN_REGION_FRACTION:
            segments[-1][1] = right
        else:
            segments.append([left, right])

    if len(segments) > 1 and (segments[0][1] - segments[0][0]) < width * MIN_REGION_FRACTION:
        segments[1][0] = segments[0][0]
        segments.pop(0)

    return [(left, 0, right - left, height) for left, right in segments]


def _tesseract_words(img: Path, box: tuple[int, int, int, int]):
    """Run tesseract in TSV mode over one region; return word rows + mean conf.

    The region is cropped before recognition so that ``--psm 6`` ("uniform
    block of text") applies within the block rather than across the page.
    """
    target = img
    if any(box):
        crop = _crop(img, *box)
        if crop is not None:
            target = crop

    with _OCR_SLOTS:
        proc = subprocess.run(
            ["tesseract", str(target), "stdout", "--psm", "6", "tsv"],
            capture_output=True,
            text=True,
            check=False,
            env=TESSERACT_ENV,
        )

    words: list[dict] = []
    confs: list[float] = []
    for line in proc.stdout.splitlines()[1:]:  # first line is the TSV header
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            left, top, width, height = (int(parts[i]) for i in (6, 7, 8, 9))
            conf = float(parts[10])
        except ValueError:
            continue
        text = parts[11]
        if not text.strip() or conf < 0:
            continue
        words.append({"left": left, "top": top, "width": width, "height": height, "text": text})
        confs.append(conf)

    return words, (sum(confs) / len(confs) if confs else None)


def _crop(img: Path, left: int, top: int, width: int, height: int) -> Path | None:
    """Write the region to its own file, or None to use the page as-is."""
    try:
        from PIL import Image
    except ImportError:
        return None

    out = img.with_name(f"{img.stem}-r{left}.png")
    with Image.open(img) as im:
        if (left, top, width, height) == (0, 0, im.width, im.height):
            return None  # a whole-page "crop" is just a re-encode
        im.crop((left, top, left + width, top + height)).save(out)
    return out


def _words_to_layout_text(words: list[dict]) -> str:
    """Rebuild column-aligned text from OCR word boxes.

    Layout handlers key off runs of 2+ spaces between fields, so word x-offsets
    are converted back into character columns using the median character width.
    """
    if not words:
        return ""
    heights = sorted(w["height"] for w in words)
    line_tol = max(heights[len(heights) // 2] * 0.6, 4)

    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["left"])):
        for line in lines:
            if abs(line[0]["top"] - word["top"]) <= line_tol:
                line.append(word)
                break
        else:
            lines.append([word])

    widths = sorted(w["width"] / max(len(w["text"]), 1) for w in words)
    char_w = max(widths[len(widths) // 2], 1.0)

    out = []
    for line in lines:
        line.sort(key=lambda w: w["left"])
        buf = ""
        for word in line:
            col = int(round(word["left"] / char_w))
            if col > len(buf):
                buf += " " * (col - len(buf))
            elif buf:
                buf += " "
            buf += word["text"]
        out.append(buf.rstrip())
    return "\n".join(out)


PAGE_FOOTER = re.compile(r"—\s*page\s+\d+\s*$", re.IGNORECASE)


def clean_lines(text: str) -> list[str]:
    """Page text -> lines with footers dropped and trailing space trimmed."""
    return [ln.rstrip() for ln in text.splitlines() if not PAGE_FOOTER.search(ln.strip())]
