"""Flask front end: pick PDFs and an output folder in a browser.

    .venv312/bin/python -m gpa_ana.web

Three tabs, opening on Help: Run holds the form, which posts to ``/run`` in the background and
reports the run's status (and any error) at the bottom of the same page; View
reads the ``rank_<id>_gpa.json`` files a run wrote and shows one category's
ranking as a table; clicking a student opens their full course list beside
the original PDF.  Help explains both.  The run itself is ``service.analyse``.
Nothing about parsing, categorisation or ranking lives here.

Scope, stated because a web form invites the opposite assumption: this is a
localhost tool for one operator.  It binds to 127.0.0.1, and the folder fields
are real server paths -- the point is to read a transcript folder already on
this machine and write results next to it.  Do not expose it to a network
without putting authentication and a path allowlist in front of it first.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, abort, jsonify, render_template, request, send_file

from .categories import available_categories, load_ruleset
from .config import Config, ConfigError
from .extract import configure_ocr_concurrency
from .layout import load_profiles
from .service import analyse

MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # a transcript is well under 1 MB

# Written into the output folder by each web run: where its PDFs live, so View
# can show the original next to the extracted courses.  The rank files only
# carry bare file names.
SOURCES_FILE = "sources.json"
UPLOADS_DIR = "pdfs"


@dataclass
class Defaults:
    """Form prefill, read from config.yaml when there is one."""

    pdf_folder: str = ""
    out_folder: str = "out"
    categories: list[str] = field(default_factory=list)
    min_category_courses: int = 0

    @classmethod
    def discover(cls, config_path: Path) -> "Defaults":
        try:
            config = Config.load(config_path)
        except ConfigError:
            # No usable config is normal here -- the browser form is the
            # alternative to having one, so fall back to empty fields.
            return cls()
        return cls(
            pdf_folder=str(config.pdf_folder),
            out_folder=str(config.out_folder),
            categories=list(config.categories),
            min_category_courses=config.min_category_courses,
        )


class FormError(ValueError):
    """A problem with what the operator typed, to show next to the form."""


def _resolve_out_folder(raw: str) -> Path:
    if not raw.strip():
        raise FormError("an output path is required")
    out = Path(raw.strip()).expanduser()
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FormError(f"output path is not writable: {exc}") from None
    return out


def _resolve_categories(selected: list[str]) -> list:
    if not selected:
        raise FormError("select at least one category")
    try:
        return [load_ruleset(name) for name in selected]
    except Exception as exc:
        raise FormError(str(exc)) from None


def _collect_pdfs(form, files, staging: Path) -> tuple[list[Path], str]:
    """Return (pdfs, description). Uploads land in ``staging``."""
    uploads = [f for f in files.getlist("pdfs") if f and f.filename]

    if uploads:
        kept = []
        for upload in uploads:
            name = Path(upload.filename).name  # never trust a client path
            if not name.lower().endswith(".pdf"):
                raise FormError(f"not a PDF: {name}")
            target = staging / name
            upload.save(target)
            kept.append(target)
        return sorted(kept), f"{len(kept)} uploaded file(s)"

    raw = (form.get("pdf_folder") or "").strip()
    if not raw:
        raise FormError("upload at least one PDF, or give a folder path")
    folder = Path(raw).expanduser()
    if not folder.is_dir():
        raise FormError(f"not a folder: {folder}")
    found = sorted(p for p in folder.glob("*.pdf") if p.is_file())
    if not found:
        raise FormError(f"no PDFs found in {folder}")
    return found, str(folder)


def _ranked_categories(folder: Path) -> dict[str, Path]:
    """{CATEGORY: rank json} for every ranking written into ``folder``."""
    if not folder.is_dir():
        return {}
    found = {}
    for path in sorted(folder.glob("rank_*_gpa.json")):
        slug = path.name[len("rank_"):-len("_gpa.json")]
        found[slug.upper()] = path
    return found


def _record_sources(out_folder: Path, pdfs: list[Path], uploaded: bool) -> None:
    """Note where this run's PDFs are; keep uploads, which are otherwise temporary."""
    if uploaded:
        kept = out_folder / UPLOADS_DIR
        kept.mkdir(exist_ok=True)
        for pdf in pdfs:
            shutil.copy2(pdf, kept / pdf.name)
        pdf_folder = kept
    else:
        pdf_folder = pdfs[0].parent
    (out_folder / SOURCES_FILE).write_text(
        json.dumps({"pdf_folder": str(pdf_folder.resolve())}, indent=2)
    )


def _find_pdf(folder: Path, name: str, config_path: Path) -> Path | None:
    """The original PDF behind a result, or None when it cannot be found.

    The folder a web run recorded comes first; a CLI run records nothing, so
    fall back to config.yaml's pdf-folder, which is where the CLI reads from.
    """
    candidates = []
    try:
        recorded = json.loads((folder / SOURCES_FILE).read_text())["pdf_folder"]
        candidates.append(Path(recorded))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    configured = Defaults.discover(config_path).pdf_folder
    if configured:
        candidates.append(Path(configured).expanduser())
    for base in candidates:
        path = base / name
        if path.is_file():
            return path
    return None


def _bare_pdf_name(raw: str | None) -> str:
    """Only a bare ``*.pdf`` name is accepted, so lookups stay inside one folder."""
    name = Path(raw or "").name
    return name if name.lower().endswith(".pdf") else ""


def create_app(config_path: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    config_path = Path(config_path or "config.yaml")

    # The written files are what the operator asked for; the download links are
    # a convenience on top. Serving only paths this process just wrote keeps
    # the convenience from turning into "read any file on the box".
    served: dict[str, Path] = {}
    # Where the last successful run wrote, so View opens on fresh results.
    last_out: dict[str, Path] = {}

    @app.get("/run")
    def index():
        return render_template(
            "index.html",
            tab="run",
            defaults=Defaults.discover(config_path),
            categories=available_categories(),
        )

    @app.get("/view")
    def view():
        default = last_out.get("folder") or Defaults.discover(config_path).out_folder
        folder = Path((request.args.get("folder") or str(default)).strip()).expanduser()
        available = _ranked_categories(folder)

        selected = (request.args.get("category") or "").upper()
        if selected not in available:
            selected = next(iter(available), "")

        ranking, error = None, None
        if selected:
            try:
                ranking = json.loads(available[selected].read_text())
            except (OSError, ValueError) as exc:
                error = f"could not read {available[selected].name}: {exc}"
        elif not folder.is_dir():
            error = f"not a folder: {folder}"
        else:
            error = f"no rankings in {folder} -- run an analysis into it first"

        return render_template(
            "view.html",
            tab="view",
            folder=folder,
            categories=list(available),
            selected=selected,
            ranking=ranking,
            error=error,
        )

    # Help is the landing page, so a first visit starts with how to use the tool.
    @app.get("/")
    @app.get("/help")
    def help_page():
        return render_template("help.html", tab="help")

    @app.get("/view/courses")
    def view_courses():
        """One student's extracted courses, for the View tab's dialog."""
        folder = Path((request.args.get("folder") or "").strip()).expanduser()
        category = (request.args.get("category") or "").upper()
        if category not in _ranked_categories(folder):
            return jsonify(error=f"no {category or 'category'} ranking in {folder}"), 404

        name = _bare_pdf_name(request.args.get("file"))
        slug = category.lower()
        path = folder / "extracted" / f"{Path(name).stem}.{slug}.courses.json"
        if not name or not path.is_file():
            return jsonify(error=f"no extracted courses for {name or 'that student'}"), 404
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            return jsonify(error=f"could not read {path.name}: {exc}"), 500

        # Course rows flag membership under the category's own key ("stem",
        # "law", ...); give the page one name for it.
        for course in record.get("courses", []):
            course["in_category"] = bool(course.pop(slug, False))

        pdf = _find_pdf(folder, name, config_path)
        record["pdf_url"] = (
            f"/view/pdf?{urlencode({'folder': str(folder), 'file': name})}" if pdf else None
        )
        return jsonify(record)

    @app.get("/view/pdf")
    def view_pdf():
        folder = Path((request.args.get("folder") or "").strip()).expanduser()
        name = _bare_pdf_name(request.args.get("file"))
        pdf = _find_pdf(folder, name, config_path) if name else None
        if pdf is None:
            abort(404)
        # Inline, so the browser's own PDF viewer shows it inside the dialog.
        return send_file(pdf, mimetype="application/pdf")

    @app.post("/run")
    def run():
        # The Run tab posts here with fetch() and shows the answer under the
        # form, so this returns a status, not a page.
        def fail(message: str):
            return jsonify(ok=False, error=message), 400

        staging = Path(tempfile.mkdtemp(prefix="gpa-ana-upload-"))
        try:
            try:
                out_folder = _resolve_out_folder(request.form.get("out_folder", ""))
                rulesets = _resolve_categories(request.form.getlist("categories"))
                pdfs, source = _collect_pdfs(request.form, request.files, staging)
            except FormError as exc:
                return fail(str(exc))

            workers = int(request.form.get("jobs") or 0) or (os.cpu_count() or 1)
            configure_ocr_concurrency(workers)

            try:
                result = analyse(
                    pdfs,
                    rulesets,
                    load_profiles(),
                    force_ocr=bool(request.form.get("force_ocr")),
                    workers=workers,
                    min_category_courses=int(request.form.get("min_courses") or 0),
                    out_folder=out_folder,
                )
            except Exception as exc:  # profile/ruleset problems are user-fixable
                return fail(str(exc))
            if result.transcripts:
                _record_sources(out_folder, pdfs, uploaded=pdfs[0].parent == staging)
        finally:
            # _record_sources kept any uploads; the staging copy has no further use.
            shutil.rmtree(staging, ignore_errors=True)

        served.clear()
        for outcome in result.categories:
            if outcome.outputs:
                for kind in ("json", "csv"):
                    served[f"{outcome.category.lower()}.{kind}"] = outcome.outputs[kind]

        if not result.transcripts:
            return fail("no transcripts could be parsed")
        last_out["folder"] = out_folder

        return jsonify(
            ok=True,
            source=source,
            pdf_count=len(pdfs),
            parsed=len(result.transcripts),
            out_folder=str(out_folder),
            categories=[
                {"name": c.category, "ranked": len(c.ranked), "held": len(c.held)}
                for c in result.categories
            ],
            failures=[{"file": f, "error": e} for f, e in result.failures],
        )

    @app.get("/download/<key>")
    def download(key: str):
        path = served.get(key)
        if path is None or not path.is_file():
            abort(404)
        return send_file(path, as_attachment=True)

    @app.get("/favicon.ico")
    def favicon():
        return "", 204  # keeps a browser's automatic request out of the log

    return app


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="gpa-ana-web", description="Browser front end for gpa-ana."
    )
    parser.add_argument("-c", "--config", type=Path, default=Path("config.yaml"),
                        help="config.yaml to prefill the form from")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    app = create_app(args.config)
    print(f"gpa-ana web UI on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
