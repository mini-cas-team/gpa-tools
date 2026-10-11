"""Flask app for running category builders and browsing their results.

    python3 -m browser_view          # http://127.0.0.1:5001

Two tabs, opening on Upload: Upload converts uploaded PDFs (or every PDF in
pdf-folder) to <name>.txt and <name>.common.json (with per-course GPA) in
config.yaml's work-folder -- the input every category builder reads.  A
Categories panel beside it lists config.yaml's category-definitions and adds
a category: category-definer drafts a definition, the operator edits and
saves it, and after a time estimate the selected builder runs on it in the
background; out_builder then publishes the result to out-folder.  View reads
one builder's ``<cat>_gpa.csv`` from <work-folder>/<builder>/ and shows it as
a sortable table; clicking a student opens their course list (from
``<cat>_courses.csv``) beside the original PDF.

This is a localhost tool for one operator -- it binds to 127.0.0.1 and serves
files from the configured folders.  Do not expose it to a network.
"""

from __future__ import annotations

import csv
import logging
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import yaml
from flask import Flask, abort, jsonify, render_template, request, send_file

import category_builder
from category_builder import definer
from category_builder.common import RunError, courses_csv_name
from category_builder.definitions import Definitions
from out_builder import publish
from upload.convert import add_tool_folders, pdf_to_common_json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # a transcript is well under 1 MB
log = logging.getLogger("browser_view")


def _load_config(config_path: Path) -> dict:
    try:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        # No usable config just means empty defaults; the folder box still works.
        return {}


def _built_categories(folder: Path) -> dict[str, Path]:
    """{CATEGORY: <cat>_gpa.csv} for every category a builder wrote into ``folder``."""
    if not folder.is_dir():
        return {}
    found = {}
    for path in sorted(folder.glob("*_gpa.csv")):
        found[path.name[:-len("_gpa.csv")].upper()] = path
    return found


def _builder_folder(cfg: dict, raw_name: str | None) -> tuple[str, Path]:
    """(builder, its work folder); the active builder when none is asked for."""
    name = raw_name or category_builder.active(cfg)
    category_builder.get(name)  # UnknownBuilder for a name not in the registry
    return name, category_builder.folder(cfg, name)


def _num(raw: str | None, kind=float):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return kind(float(raw))
    except ValueError:
        return None


def _student_row(row: dict, category: str) -> dict:
    """One <cat>_gpa.csv row, under the names the View template uses."""
    slug = category.lower()
    return {
        "student": row.get("student") or "",
        "institution": row.get("institution") or "",
        "program": row.get("program") or "",
        "gpa_capped": _num(row.get(f"{category}_GPA")),
        "overall_gpa_capped": _num(row.get("all_courses_GPA")),
        "category_courses": _num(row.get(f"{slug}_courses"), int),
        "total_courses": _num(row.get("total_courses"), int),
        "category_course_pct": _num(row.get(f"{slug}_all_pct")),
        "file": row.get("file") or "",
    }


def _read_students(path: Path, category: str) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [_student_row(r, category) for r in csv.DictReader(fh)]


def _courses_from_csv(path: Path, category: str, name: str) -> list[dict]:
    flag = f"is_{category.lower()}"
    courses = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("file") != name:
                continue
            courses.append({
                "term": row.get("term") or "",
                "code": row.get("code") or "",
                "title": row.get("title") or "",
                "grade": row.get("grade") or "",
                "credits": _num(row.get("credits")),
                "grade_points_4pt": _num(row.get("gpa_4")),
                "in_category": row.get(flag) == "yes",
                # Builders write only courses that count toward GPA.
                "counts_toward_gpa": True,
            })
    return courses


def _collect_pdfs(files, pdf_folder: str, staging: Path) -> tuple[list[Path], str]:
    """Uploaded PDFs (saved into ``staging``), else every PDF in pdf-folder."""
    uploads = [f for f in files.getlist("pdfs") if f and f.filename]
    if uploads:
        kept = []
        for upload in uploads:
            name = Path(upload.filename).name  # never trust a client path
            if not name.lower().endswith(".pdf"):
                raise ValueError(f"not a PDF: {name}")
            upload.save(staging / name)
            kept.append(staging / name)
        return sorted(kept), f"{len(kept)} uploaded file(s)"
    if not pdf_folder:
        raise ValueError("upload at least one PDF, or set pdf-folder in config.yaml")
    folder = Path(pdf_folder).expanduser()
    found = sorted(p for p in folder.glob("*.pdf") if p.is_file())
    if not found:
        raise ValueError(f"no PDFs found in {folder}")
    return found, str(folder)


def _find_pdf(name: str, cfg: dict) -> Path | None:
    """The original PDF behind a result: work-folder, then config.yaml's pdf-folder."""
    candidates = []
    if cfg.get("work-folder"):  # the Upload tab keeps uploaded PDFs there
        candidates.append(Path(cfg["work-folder"]).expanduser())
    if cfg.get("pdf-folder"):
        candidates.append(Path(cfg["pdf-folder"]).expanduser())
    for base in candidates:
        path = base / name
        if path.is_file():
            return path
    return None


def _bare_pdf_name(raw: str | None) -> str:
    """Only a bare ``*.pdf`` name is accepted, so lookups stay inside one folder."""
    name = Path(raw or "").name
    return name if name.lower().endswith(".pdf") else ""


# Used in file names (<name>_gpa.csv) and CSV headers, so keep it plain.
CATEGORY_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
CATEGORY_NAME_RULE = "use letters, digits and _, starting with a letter (at most 40)"


class CategoryJob:
    """The one background builder run the Upload tab can start, then its publish.

    One at a time across all builders: Ollama answers one question at a time,
    so a second run would only queue behind the first.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.state: dict = {"status": "idle"}

    def snapshot(self) -> dict:
        with self.lock:
            state = dict(self.state)
        if state.get("started"):
            state["elapsed"] = round((state.get("finished") or time.time()) - state["started"])
        return state

    def start(self, raw: dict, builder: str, cfg: dict, category: str) -> bool:
        """Run ``builder`` (its load_config result is ``cfg``), then publish to out-folder."""
        with self.lock:
            if self.state.get("status") == "running":
                return False
            self.state = {"status": "running", "builder": builder, "category": category,
                          "done": 0, "total": None, "started": time.time()}
        threading.Thread(target=self._run, args=(raw, builder, cfg, category), daemon=True).start()
        return True

    def _progress(self, done: int, total: int) -> None:
        with self.lock:
            self.state.update(done=done, total=total)

    def _run(self, raw: dict, builder: str, cfg: dict, category: str) -> None:
        try:
            gpa_csv = category_builder.get(builder).run(cfg, category, progress=self._progress)
            published = publish(raw, builder, category)
        except Exception as exc:  # shown on the page; the thread must not die silently
            log.exception("%s run of %s failed", builder, category)
            update = {"status": "error", "error": str(exc)}
        else:
            log.info("%s run of %s done; published %s", builder, category, published)
            update = {"status": "done", "folder": str(gpa_csv.parent),
                      "published": str(published)}
        with self.lock:
            self.state.update(update, finished=time.time())


def create_app(config_path: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    config_path = Path(config_path or PROJECT_ROOT / "config.yaml")
    add_tool_folders(_load_config(config_path).get("tool-folders"))

    @app.get("/")
    def upload():
        cfg = _load_config(config_path)
        return render_template("upload.html", tab="upload",
                               pdf_folder=cfg.get("pdf-folder") or "",
                               work_folder=cfg.get("work-folder") or "")

    job = CategoryJob()

    def category_name(raw: str | None) -> str:
        name = (raw or "").strip().upper()
        if not CATEGORY_NAME.match(name):
            raise ValueError(CATEGORY_NAME_RULE)
        return name

    @app.get("/categories")
    def list_categories():
        """{name: definition} from config.yaml's category-definitions section.

        ``names`` keeps the file's order (jsonify sorts the object's keys).
        """
        try:
            data = Definitions(config_path).data
            return jsonify(categories=data, names=list(data), path=str(config_path),
                           builders=category_builder.names(),
                           active_builder=category_builder.active(_load_config(config_path)))
        except (OSError, yaml.YAMLError) as exc:
            return jsonify(error=f"could not read {config_path}: {exc}"), 500

    @app.post("/categories/draft")
    def draft_category():
        """category-definer's proposed definition for a new category. Nothing is saved."""
        try:
            name = category_name(request.form.get("name"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        if name in Definitions(config_path).data:
            return jsonify(error=f"{name} is already defined"), 409
        cfg = _load_config(config_path)
        try:
            definer_cfg = definer.load_config(cfg)
            definition = definer.client(definer_cfg).define(name)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except (OSError, KeyError) as exc:  # URLError is an OSError
            return jsonify(error=f"{definer.describe(definer_cfg)}: {exc}"), 502
        return jsonify(name=name, definition=definition)

    @app.post("/categories")
    def save_category():
        """Save a (possibly edited) definition into config.yaml.

        A new name unless ``replace=1`` (Update), which needs an existing one.
        """
        try:
            name = category_name(request.form.get("name"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        definition = (request.form.get("definition") or "").strip()
        if not definition:
            return jsonify(error="the definition is empty"), 400
        definitions = Definitions(config_path)
        replace = request.form.get("replace") == "1"
        if replace and name not in definitions.data:
            return jsonify(error=f"{name} is not defined"), 404
        if not replace and name in definitions.data:
            return jsonify(error=f"{name} is already defined"), 409
        try:
            definitions.set(name, definition)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return jsonify(error=f"could not save to {config_path}: {exc}"), 500
        log.info("category %s saved to %s", name, config_path)
        return jsonify(name=name, categories=definitions.data)

    def chosen_builder(raw: str | None):
        """(name, module, its config) for the builder a request names, else the active one."""
        name = raw or category_builder.active(_load_config(config_path))
        module = category_builder.get(name)
        return name, module, module.load_config(config_path)

    @app.get("/runs/estimate")
    def run_estimate():
        """How long building a saved category would take now (when the builder can tell)."""
        try:
            name = category_name(request.args.get("category"))
            definition = Definitions(config_path).data.get(name)
            if definition is None:
                return jsonify(error=f"{name} is not defined"), 404
            builder, module, cfg = chosen_builder(request.args.get("builder"))
            if not hasattr(module, "estimate"):
                return jsonify(category=name, builder=builder, seconds=None, pending=None)
            return jsonify(category=name, builder=builder,
                           **module.estimate(cfg, name, definition))
        except (ValueError, RunError, OSError, KeyError) as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/runs")
    def start_run():
        try:
            name = category_name(request.form.get("category"))
            builder, _, cfg = chosen_builder(request.form.get("builder"))
        except (ValueError, OSError, KeyError) as exc:
            return jsonify(error=str(exc)), 400
        if name not in Definitions(config_path).data:
            return jsonify(error=f"{name} is not defined"), 404
        if not job.start(_load_config(config_path), builder, cfg, name):
            return jsonify(error="a category run is already in progress", **job.snapshot()), 409
        log.info("%s run of %s started", builder, name)
        return jsonify(job.snapshot())

    @app.get("/runs/status")
    def run_status():
        return jsonify(job.snapshot())

    @app.post("/upload")
    def run_upload():
        """Convert the uploaded PDFs (or all of pdf-folder) into work-folder.

        The Upload tab posts here with fetch() and shows the answer under the
        form, so this returns a status, not a page.
        """
        cfg = _load_config(config_path)
        if not cfg.get("work-folder"):
            return jsonify(ok=False, error="set work-folder in config.yaml"), 400
        work = Path(cfg["work-folder"]).expanduser()
        staging = Path(tempfile.mkdtemp(prefix="browser-view-upload-"))
        try:
            try:
                work.mkdir(parents=True, exist_ok=True)
                pdfs, source = _collect_pdfs(request.files, cfg.get("pdf-folder") or "", staging)
            except (OSError, ValueError) as exc:
                return jsonify(ok=False, error=str(exc)), 400

            converted, failures = [], []
            for pdf in pdfs:
                try:
                    if pdf.parent == staging:
                        # Keep uploads next to their txt/json so View can show them.
                        shutil.copy2(pdf, work / pdf.name)
                    doc = pdf_to_common_json(pdf, work)
                except (OSError, ValueError) as exc:
                    failures.append({"file": pdf.name, "error": str(exc)})
                    log.error("upload %s: %s", pdf.name, exc)
                    continue
                converted.append({"file": pdf.name, "warnings": doc.get("warnings") or []})
            log.info("upload: %d of %d converted from %s into %s",
                     len(converted), len(pdfs), source, work)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        return jsonify(ok=bool(converted), source=source, pdf_count=len(pdfs),
                       work_folder=str(work), converted=converted, failures=failures,
                       error=None if converted else "no PDF could be converted")

    @app.get("/view")
    def view():
        cfg = _load_config(config_path)
        builders = category_builder.names()
        try:
            builder, folder = _builder_folder(cfg, request.args.get("builder"))
        except (category_builder.UnknownBuilder, KeyError) as exc:
            return render_template("view.html", tab="view", builders=builders,
                                   builder=category_builder.active(cfg), categories=[],
                                   selected="", students=None, folder="", min_courses=0,
                                   error=str(exc).strip("'\""))
        available = _built_categories(folder)

        selected = (request.args.get("category") or "").upper()
        if selected not in available:
            selected = next(iter(available), "")

        students, error = None, None
        if selected:
            try:
                students = _read_students(available[selected], selected)
            except (OSError, ValueError, csv.Error) as exc:
                error = f"could not read {available[selected].name}: {exc}"
        else:
            error = f"the {builder} builder has no results (*_gpa.csv) in {folder} yet"

        return render_template(
            "view.html",
            tab="view",
            builders=builders,
            builder=builder,
            folder=folder,
            categories=list(available),
            selected=selected,
            students=students,
            min_courses=int(cfg.get("min-category-courses") or 0),
            error=error,
        )

    @app.get("/view/courses")
    def view_courses():
        """One student's courses, for the View tab's dialog."""
        cfg = _load_config(config_path)
        try:
            builder, folder = _builder_folder(cfg, request.args.get("builder"))
        except (category_builder.UnknownBuilder, KeyError) as exc:
            return jsonify(error=str(exc).strip("'\"")), 404
        category = (request.args.get("category") or "").upper()
        available = _built_categories(folder)
        if category not in available:
            return jsonify(error=f"no {category or 'category'} results from {builder}"), 404

        name = _bare_pdf_name(request.args.get("file"))
        try:
            students = _read_students(available[category], category)
            record = next((r for r in students if r["file"] == name), None)
            if not name or record is None:
                return jsonify(error=f"{name or 'that student'} is not in these results"), 404

            courses_csv = folder / courses_csv_name(category)
            if not courses_csv.is_file():
                return jsonify(error=f"no course list for {name} in {folder}"), 404
            courses = _courses_from_csv(courses_csv, category, name)
        except (OSError, ValueError, csv.Error) as exc:
            return jsonify(error=f"could not read courses: {exc}"), 500

        pdf = _find_pdf(name, cfg)
        return jsonify({
            **record,
            "courses": courses,
            "credit_label": "Credits",
            "pdf_url": f"/view/pdf?{urlencode({'file': name})}" if pdf else None,
        })

    @app.get("/view/pdf")
    def view_pdf():
        name = _bare_pdf_name(request.args.get("file"))
        pdf = _find_pdf(name, _load_config(config_path)) if name else None
        if pdf is None:
            abort(404)
        # Inline, so the browser's own PDF viewer shows it inside the dialog.
        return send_file(pdf, mimetype="application/pdf")

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    return app


def setup_logging(cfg: dict) -> Path:
    """Requests and errors also go to <log-folder>/browser_view.log (appended)."""
    folder = Path(cfg.get("log-folder") or "log").expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "browser_view.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    # With any handler present werkzeug stops adding its own console one.
    root.addHandler(logging.StreamHandler())
    root.setLevel(logging.INFO)
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python3 -m browser_view", description=__doc__.split("\n")[0])
    parser.add_argument("-c", "--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost)")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    log_path = setup_logging(_load_config(args.config))
    app = create_app(args.config)
    print(f"Browser View on http://{args.host}:{args.port} (log: {log_path})")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0
