"""Flask app for browsing category rankings.

    python3 -m browser_view          # http://127.0.0.1:5001

Two tabs, opening on Upload: Upload converts uploaded PDFs (or every PDF in
pdf-folder) to <name>.txt and <name>.common.json (with per-course GPA) in
config.yaml's work-folder -- the input llm_category reads.  A Categories panel
beside it lists config.yaml's category-definitions and adds a category: the
LLM drafts a definition, the operator edits and saves it, and after a time
estimate llm_category ranks every student on it in the background.  View reads the
``rank_<id>_gpa.csv`` files in a results folder and shows one category's
ranking as a table; clicking a student opens their course list beside the
original PDF.  View is copied from gpa_ana's web UI, but reads the ranking
CSV instead of gpa_ana's JSON, because llm_category and gpa_ana write that
CSV with the same columns.

Courses for the student dialog come from llm_category's
``<id>_courses.csv``; a gpa_ana results folder has no such file, so its
``extracted/<student>.<id>.courses.json`` is used instead.

Independent of gpa_ana: nothing is imported from it.  Like gpa_ana's UI,
this is a localhost tool for one operator -- it binds to 127.0.0.1 and the
folder field is a real server path.  Do not expose it to a network.
"""

from __future__ import annotations

import csv
import json
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

import llm_category.__main__ as llm_run
from llm_category.llm import OllamaClient
from llm_category.store import Definitions
from upload.convert import add_tool_folders, pdf_to_common_json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # a transcript is well under 1 MB
log = logging.getLogger("browser_view")
# Written by gpa_ana's web runs: where that run's PDFs live.
SOURCES_FILE = "sources.json"


def _load_config(config_path: Path) -> dict:
    try:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        # No usable config just means empty defaults; the folder box still works.
        return {}


def _default_folder(cfg: dict) -> Path:
    """llm_category's results when they exist, else the plain out-folder."""
    out = Path(cfg.get("out-folder") or "out").expanduser()
    return out / "llm_category" if (out / "llm_category").is_dir() else out


def _ranked_categories(folder: Path) -> dict[str, Path]:
    """{CATEGORY: rank csv} for every ranking written into ``folder``."""
    if not folder.is_dir():
        return {}
    found = {}
    for path in sorted(folder.glob("rank_*_gpa.csv")):
        slug = path.name[len("rank_"):-len("_gpa.csv")]
        found[slug.upper()] = path
    return found


def _num(raw: str | None, kind=float):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return kind(float(raw))
    except ValueError:
        return None


def _student_row(row: dict, category: str) -> dict:
    """One ranking CSV row, under the names the copied View template uses."""
    slug = category.lower()
    return {
        "rank": _num(row.get("rank"), int),
        "student": row.get("student") or "",
        "institution": row.get("institution") or "",
        "program": row.get("program") or "",
        "gpa_capped": _num(row.get(f"{category}_GPA")),
        "overall_gpa_capped": _num(row.get("all_courses_GPA")),
        "category_courses": _num(row.get(f"{slug}_courses"), int),
        "total_courses": _num(row.get("total_courses"), int),
        "category_course_pct": _num(row.get(f"{slug}_all_pct")),
        "gate": (row.get("gate") or "").strip(),
        "file": row.get("file") or "",
    }


def _read_ranking(path: Path, category: str) -> dict:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = [_student_row(r, category) for r in csv.DictReader(fh)]
    # gpa_ana's CSV holds only ranked students; llm_category's also lists the
    # ones below the course minimum, with gate=fail and no rank.
    ranked = [r for r in rows if r["gate"] != "fail" and r["rank"] is not None]
    held = [
        {**r, "reason": f"only {r['category_courses'] or 0} {category} course(s), "
                        "below the minimum"}
        for r in rows if r not in ranked
    ]
    return {"category": category, "ranked": ranked, "held_back": held}


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
                # llm_category writes only courses that count toward GPA.
                "counts_toward_gpa": True,
            })
    return courses


def _courses_from_extracted(folder: Path, category: str, name: str) -> list[dict] | None:
    slug = category.lower()
    path = folder / "extracted" / f"{Path(name).stem}.{slug}.courses.json"
    if not path.is_file():
        return None
    courses = json.loads(path.read_text(encoding="utf-8")).get("courses", [])
    # gpa_ana flags membership under the category's own key ("stem", "law", ...).
    for course in courses:
        course["in_category"] = bool(course.pop(slug, False))
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


def _find_pdf(folder: Path, name: str, cfg: dict) -> Path | None:
    """The original PDF behind a result: work-folder, the folder a gpa_ana web
    run recorded, then config.yaml's pdf-folder."""
    candidates = []
    if cfg.get("work-folder"):  # the Upload tab keeps uploaded PDFs there
        candidates.append(Path(cfg["work-folder"]).expanduser())
    try:
        candidates.append(Path(json.loads((folder / SOURCES_FILE).read_text(encoding="utf-8"))["pdf_folder"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
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


# Used in file names (rank_<name>_gpa.csv) and CSV headers, so keep it plain.
CATEGORY_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
CATEGORY_NAME_RULE = "use letters, digits and _, starting with a letter (at most 40)"


class CategoryJob:
    """The one background llm_category run the Upload tab can start.

    Ollama answers one question at a time, so a second run would only queue
    behind the first; one at a time is enforced instead.
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

    def start(self, cfg: dict, category: str) -> bool:
        with self.lock:
            if self.state.get("status") == "running":
                return False
            self.state = {"status": "running", "category": category, "done": 0, "total": None,
                          "started": time.time()}
        threading.Thread(target=self._run, args=(cfg, category), daemon=True).start()
        return True

    def _progress(self, done: int, total: int) -> None:
        with self.lock:
            self.state.update(done=done, total=total)

    def _run(self, cfg: dict, category: str) -> None:
        try:
            rank_csv, _ = llm_run.run(cfg, category, progress=self._progress)
        except Exception as exc:  # shown on the page; the thread must not die silently
            log.exception("category run %s failed", category)
            update = {"status": "error", "error": str(exc)}
        else:
            update = {"status": "done", "folder": str(rank_csv.parent)}
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
            return jsonify(categories=data, names=list(data), path=str(config_path))
        except (OSError, yaml.YAMLError) as exc:
            return jsonify(error=f"could not read {config_path}: {exc}"), 500

    @app.post("/categories/draft")
    def draft_category():
        """The LLM's proposed definition for a new category. Nothing is saved."""
        try:
            name = category_name(request.form.get("name"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        if name in Definitions(config_path).data:
            return jsonify(error=f"{name} is already defined"), 409
        cfg = llm_run.load_config(config_path)
        client = OllamaClient(cfg["url"], cfg["model"])
        try:
            definition = client.define(name)
        except (OSError, ValueError, KeyError) as exc:  # URLError is an OSError
            return jsonify(error=f"{cfg['model']} at {cfg['url']}: {exc}"), 502
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

    @app.get("/runs/estimate")
    def run_estimate():
        """How long ranking students on a saved category would take now."""
        try:
            name = category_name(request.args.get("category"))
            definition = Definitions(config_path).data.get(name)
            if definition is None:
                return jsonify(error=f"{name} is not defined"), 404
            return jsonify(category=name,
                           **llm_run.estimate(llm_run.load_config(config_path), name, definition))
        except (ValueError, llm_run.RunError, OSError, KeyError) as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/runs")
    def start_run():
        try:
            name = category_name(request.form.get("category"))
            cfg = llm_run.load_config(config_path)
        except (ValueError, OSError, KeyError) as exc:
            return jsonify(error=str(exc)), 400
        if name not in Definitions(config_path).data:
            return jsonify(error=f"{name} is not defined"), 404
        if not job.start(cfg, name):
            return jsonify(error="a category run is already in progress", **job.snapshot()), 409
        log.info("category run %s started", name)
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
        default = _default_folder(_load_config(config_path))
        folder = Path((request.args.get("folder") or str(default)).strip()).expanduser()
        available = _ranked_categories(folder)

        selected = (request.args.get("category") or "").upper()
        if selected not in available:
            selected = next(iter(available), "")

        ranking, error = None, None
        if selected:
            try:
                ranking = _read_ranking(available[selected], selected)
            except (OSError, ValueError, csv.Error) as exc:
                error = f"could not read {available[selected].name}: {exc}"
        elif not folder.is_dir():
            error = f"not a folder: {folder}"
        else:
            error = f"no rankings (rank_*_gpa.csv) in {folder}"

        return render_template(
            "view.html",
            tab="view",
            folder=folder,
            categories=list(available),
            selected=selected,
            ranking=ranking,
            error=error,
        )

    @app.get("/view/courses")
    def view_courses():
        """One student's courses, for the View tab's dialog."""
        folder = Path((request.args.get("folder") or "").strip()).expanduser()
        category = (request.args.get("category") or "").upper()
        available = _ranked_categories(folder)
        if category not in available:
            return jsonify(error=f"no {category or 'category'} ranking in {folder}"), 404

        name = _bare_pdf_name(request.args.get("file"))
        try:
            ranking = _read_ranking(available[category], category)
            record = next(
                (r for r in ranking["ranked"] + ranking["held_back"] if r["file"] == name), None
            )
            if not name or record is None:
                return jsonify(error=f"{name or 'that student'} is not in this ranking"), 404

            courses_csv = folder / f"{category.lower()}_courses.csv"
            if courses_csv.is_file():
                courses = _courses_from_csv(courses_csv, category, name)
            else:
                courses = _courses_from_extracted(folder, category, name)
            if courses is None:
                return jsonify(error=f"no course list for {name} in {folder}"), 404
        except (OSError, ValueError, csv.Error) as exc:
            return jsonify(error=f"could not read courses: {exc}"), 500

        pdf = _find_pdf(folder, name, _load_config(config_path))
        return jsonify({
            **record,
            "courses": courses,
            "credit_label": "Credits",
            "pdf_url": f"/view/pdf?{urlencode({'folder': str(folder), 'file': name})}"
            if pdf else None,
        })

    @app.get("/view/pdf")
    def view_pdf():
        folder = Path((request.args.get("folder") or "").strip()).expanduser()
        name = _bare_pdf_name(request.args.get("file"))
        pdf = _find_pdf(folder, name, _load_config(config_path)) if name else None
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
