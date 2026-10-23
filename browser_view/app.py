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
from letter_builder import RANKS_CSV, letters_folder, rank_letters, students as letter_students
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


class BackgroundJob:
    """One background run at a time, and the state the page polls while it runs."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state: dict = {"status": "idle"}

    def snapshot(self) -> dict:
        with self.lock:
            state = dict(self.state)
        if state.get("started"):
            state["elapsed"] = round((state.get("finished") or time.time()) - state["started"])
        return state

    def _start(self, args: tuple, **state) -> bool:
        """Run ``self._run(*args)`` in a thread, unless a run is already going."""
        with self.lock:
            if self.state.get("status") == "running":
                return False
            self.state = {"status": "running", **state, "started": time.time()}
        threading.Thread(target=self._run, args=args, daemon=True).start()
        return True

    def _update(self, **changes) -> None:
        with self.lock:
            self.state.update(changes)

    def _finish(self, **changes) -> None:
        self._update(**changes, finished=time.time())


class CategoryJob(BackgroundJob):
    """The one background builder run the Upload tab can start, then its publish.

    One at a time across all builders: Ollama answers one question at a time,
    so a second run would only queue behind the first.
    """

    def start(self, raw: dict, builder: str, cfg: dict, category: str) -> bool:
        """Run ``builder`` (its load_config result is ``cfg``), then publish to out-folder."""
        return self._start((raw, builder, cfg, category), builder=builder, category=category,
                           done=0, total=None)

    def _progress(self, done: int, total: int) -> None:
        self._update(done=done, total=total)

    def _run(self, raw: dict, builder: str, cfg: dict, category: str) -> None:
        try:
            gpa_csv = category_builder.get(builder).run(cfg, category, progress=self._progress)
            published = publish(raw, builder, category)
        except Exception as exc:  # shown on the page; the thread must not die silently
            log.exception("%s run of %s failed", builder, category)
            self._finish(status="error", error=str(exc))
        else:
            log.info("%s run of %s done; published %s", builder, category, published)
            self._finish(status="done", folder=str(gpa_csv.parent), published=str(published))


class LetterJob(BackgroundJob):
    """The one background run that converts recommendation letters and ranks them.

    Each Jev call takes about a second, so a folder of letters takes minutes;
    the FTP Upload page polls /letters/status meanwhile.  The ranks are merged
    into <letters-folder>/letter_ranks.csv when the run ends.
    """

    def start(self, client, pdfs: list[Path], ranks_csv: Path, skipped: list[str]) -> bool:
        return self._start((client, pdfs, ranks_csv), done=0, total=len(pdfs), skipped=skipped)

    def _step(self) -> None:
        with self.lock:
            self.state["done"] += 1

    def _run(self, client, pdfs: list[Path], ranks_csv: Path) -> None:
        try:
            ranks, failures = rank_letters.rank_pdfs(client, pdfs, progress=self._step)
            if ranks:
                rank_letters.merge_ranks(ranks_csv, ranks)
        except Exception as exc:  # shown on the page; the thread must not die silently
            log.exception("letter ranking failed")
            self._finish(status="error", error=str(exc), failures=[])
            return
        for failure in failures:
            log.error("letter %s: %s", failure["file"], failure["error"])
        log.info("letters: ranked %d of %d into %s", len(ranks), len(pdfs), ranks_csv)
        self._finish(status="done" if ranks else "error", ranked=len(ranks), failures=failures,
                     error=None if ranks else "no letter could be ranked")


def _student_name(file: str) -> str:
    """adam_flores.pdf -> Adam Flores."""
    return Path(file).stem.replace("_", " ").title()


def _transcript_names(cfg: dict) -> list[str]:
    """Every transcript PDF's name, from work-folder and pdf-folder (as _find_pdf looks)."""
    names = set()
    for key in ("work-folder", "pdf-folder"):
        if cfg.get(key):
            names.update(p.name for p in Path(cfg[key]).expanduser().glob("*.pdf"))
    return sorted(names)


def _letter_records(cfg: dict, transcripts: list[str]) -> tuple[list[dict], str | None]:
    """(student_info records for ``transcripts`` and every letter in letters-folder,
    an error message when the letters could not be read)."""
    folder = letters_folder(cfg)
    try:
        return letter_students.folder_info(folder, transcripts), None
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        log.error("letters from %s: %s", folder, exc)
        return letter_students.student_info(transcripts, []), f"could not read the letters in {folder}: {exc}"


def _letter_students(cfg: dict, names: dict[str, str]) -> tuple[list[dict], str | None]:
    """Recommendations → View rows, one per student with letters, and any read error.

    ``names`` maps a transcript file to the student's name from category results;
    a student not in it is named after their file."""
    records, error = _letter_records(cfg, _transcript_names(cfg))
    return [{"student": names.get(r["transcript"]) or _student_name(r["student"]),
             "file": r["transcript"] or f"{r['student']}.pdf",
             "has_transcript": r["transcript"] is not None,
             "letters": len(r["letters"]), "group_rank": r["group_rank"],
             "rank_order": letter_students.group_rank_order(r["group_rank"])}
            for r in records if r["letters"]], error


def _add_letter_ranks(students: list[dict], cfg: dict) -> None:
    """Set each student's ``letter_rank`` (student_info's group_rank of their
    recommendation letters, matched by file name) and its ``letter_rank_order``."""
    records, _ = _letter_records(cfg, [s["file"] for s in students if s["file"]])
    by_transcript = {r["transcript"]: r["group_rank"] for r in records if r["transcript"]}
    for s in students:
        s["letter_rank"] = by_transcript.get(s["file"])
        s["letter_rank_order"] = letter_students.group_rank_order(s["letter_rank"])


def _dev_mode(cfg: dict) -> bool:
    """config.yaml's run_mode is dev: the UI also shows what is otherwise hidden."""
    return str(cfg.get("run_mode") or "").strip().lower() == "dev"


def _student_letters(transcript: str, cfg: dict) -> dict:
    """{"letters": [{file, rank, pdf_url}], "letter_rank": group rank} for one
    student.  In dev run_mode each letter also has Jev's own rank (``jev_rank``)
    and its ``confidence``."""
    records, _ = _letter_records(cfg, [transcript])
    record = next(r for r in records if r["transcript"] == transcript)
    hidden = () if _dev_mode(cfg) else ("jev_rank", "confidence")
    letters = [{**{k: v for k, v in letter.items() if k not in hidden},
                "pdf_url": f"/letters/pdf?{urlencode({'file': letter['file']})}"}
               for letter in record["letters"]]
    return {"letter_rank": record["group_rank"], "letters": letters}


def create_app(config_path: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    config_path = Path(config_path or PROJECT_ROOT / "config.yaml")
    add_tool_folders(_load_config(config_path).get("tool-folders"))

    # The Transcripts page shows both panels; each sub-item shows one of them.
    @app.get("/", defaults={"panel": None})
    @app.get("/transcripts/<any('ftp-upload', 'category-build'):panel>")
    def upload(panel):
        cfg = _load_config(config_path)
        return render_template("upload.html", tab=panel,
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
            cfg = _load_config(config_path)
            try:
                steps = definer.step_names(definer.load_config(cfg))
            except ValueError:
                steps = []  # a bad mode: the draft request reports it
            return jsonify(categories=data, names=list(data), path=str(config_path),
                           builders=category_builder.names(),
                           active_builder=category_builder.active(cfg), definer=steps)
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
            definer.load_config(cfg)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        try:
            draft = definer.draft(cfg, name)
        except definer.DraftFailed as exc:
            # Every try failed; the page lists them (they are in category_definer.log too).
            return jsonify(error=f"category-definer could not draft {name}: every step failed",
                           attempts=[a.to_dict() for a in exc.attempts]), 502
        return jsonify(name=name, definition=draft.definition, source=draft.source,
                       attempts=[a.to_dict() for a in draft.attempts])

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
                _add_letter_ranks(students, cfg)
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

    @app.get("/view/student")
    def student_detail():
        """One student's details for the student dialog: the transcript's PDF, its
        courses when a category is given, and the recommendation letters with
        their post_rank ranks."""
        cfg = _load_config(config_path)
        name = _bare_pdf_name(request.args.get("file"))
        if not name:
            return jsonify(error="that student is not known"), 404
        detail = {"student": _student_name(name), "file": name, "courses": None}

        if request.args.get("category"):
            try:
                builder, folder = _builder_folder(cfg, request.args.get("builder"))
            except (category_builder.UnknownBuilder, KeyError) as exc:
                return jsonify(error=str(exc).strip("'\"")), 404
            category = request.args["category"].upper()
            available = _built_categories(folder)
            if category not in available:
                return jsonify(error=f"no {category} results from {builder}"), 404
            try:
                students = _read_students(available[category], category)
                record = next((r for r in students if r["file"] == name), None)
                if record is None:
                    return jsonify(error=f"{name} is not in these results"), 404
                courses_csv = folder / courses_csv_name(category)
                if not courses_csv.is_file():
                    return jsonify(error=f"no course list for {name} in {folder}"), 404
                courses = _courses_from_csv(courses_csv, category, name)
            except (OSError, ValueError, csv.Error) as exc:
                return jsonify(error=f"could not read courses: {exc}"), 500
            detail.update(record, courses=courses, credit_label="Credits")

        pdf = _find_pdf(name, cfg)
        detail["pdf_url"] = f"/view/pdf?{urlencode({'file': name})}" if pdf else None
        return jsonify({**detail, **_student_letters(name, cfg)})

    @app.get("/view/pdf")
    def view_pdf():
        name = _bare_pdf_name(request.args.get("file"))
        pdf = _find_pdf(name, _load_config(config_path)) if name else None
        if pdf is None:
            abort(404)
        # Inline, so the browser's own PDF viewer shows it inside the dialog.
        return send_file(pdf, mimetype="application/pdf")

    letter_job = LetterJob()

    @app.get("/letters/ftp-upload")
    def letters_upload():
        folder = letters_folder(_load_config(config_path))
        return render_template("letters_upload.html", tab="letters-upload", folder=folder)

    @app.post("/letters/upload")
    def run_letters_upload():
        """Save the uploaded letters (else take every PDF in letters-folder), then
        convert and rank them in the background; /letters/status reports progress.

        A letter with a <name>.txt was uploaded before: it is skipped, not replaced."""
        cfg = _load_config(config_path)
        folder = letters_folder(cfg)
        try:
            client = rank_letters.jev_client(cfg)
            folder.mkdir(parents=True, exist_ok=True)
            done = {p.stem for p in folder.glob("*.txt")}
            uploads = [f for f in request.files.getlist("pdfs") if f and f.filename]
            pdfs, skipped = [], []
            if uploads:
                names = [Path(u.filename).name for u in uploads]  # never trust a client path
                bad = [n for n in names if not n.lower().endswith(".pdf")]
                if bad:
                    raise ValueError(f"not a PDF: {bad[0]}")
                state = letter_job.snapshot()
                if state["status"] == "running":  # its letters must not change under it
                    return jsonify(error="letters are already being ranked", **state), 409
                for upload, name in zip(uploads, names):
                    if Path(name).stem in done:
                        skipped.append(name)
                    elif folder / name not in pdfs:
                        upload.save(folder / name)
                        pdfs.append(folder / name)
            else:
                found = sorted(p for p in folder.glob("*.pdf") if p.is_file())
                if not found:
                    raise ValueError(f"no PDFs found in {folder}")
                pdfs = [p for p in found if p.stem not in done]
                skipped = [p.name for p in found if p.stem in done]
        except (RunError, OSError, ValueError) as exc:
            return jsonify(error=str(exc)), 400
        if not pdfs:
            return jsonify(status="skipped", skipped=skipped)
        if not letter_job.start(client, sorted(pdfs), folder / RANKS_CSV, sorted(skipped)):
            return jsonify(error="letters are already being ranked", **letter_job.snapshot()), 409
        log.info("letters: ranking %d PDF(s) in %s", len(pdfs), folder)
        return jsonify(letter_job.snapshot())

    @app.get("/letters/status")
    def letters_status():
        return jsonify(letter_job.snapshot())

    @app.get("/letters/view")
    def letters_view():
        cfg = _load_config(config_path)
        folder = letters_folder(cfg)
        # The dialog shows the transcript's courses from the active builder's first
        # category; its results also give the students' full names.
        names = {}
        try:
            builder, built = _builder_folder(cfg, None)
            available = _built_categories(built)
            category = next(iter(available), "")
            if category:
                names = {r["file"]: r["student"] for r in _read_students(available[category], category)}
        except (category_builder.UnknownBuilder, KeyError, OSError, ValueError, csv.Error):
            builder, category = "", ""
        rows, error = _letter_students(cfg, names)
        if not rows and not error:
            error = f"no recommendation letters in {folder} yet"
        return render_template("letters_view.html", tab="letters-view", folder=folder,
                               students=rows, group_ranks=letter_students.GROUP_RANKS,
                               builder=builder, category=category, error=error)

    @app.get("/letters/pdf")
    def letters_pdf():
        name = _bare_pdf_name(request.args.get("file"))
        pdf = letters_folder(_load_config(config_path)) / name if name else None
        if pdf is None or not pdf.is_file():
            abort(404)
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
