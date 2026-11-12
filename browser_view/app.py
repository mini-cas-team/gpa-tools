"""Flask app for running category builders and browsing their results.

    python3 -m browser_view          # http://127.0.0.1:5001

Upload > Transcripts converts uploaded PDFs (saved in <input-folder>/transcripts,
or every PDF already there) to <name>.txt and <name>.common.json (with per-course
GPA) in <work-folder>/transcripts -- the input every category builder reads.  A
Category Build panel beside it lists the local definitions repo and adds
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
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file

import category_builder
import folders
from category_builder import definer
from category_builder.common import RunError, courses_csv_name
from category_builder.definitions import Definitions
from letter_builder import RANKS_CSV, letters_folder, letters_work_folder, rank_letters, students as letter_students
from letter_builder.students import student_key
import rank_ai
from out_builder import publish
from profile import display as profile_display, enrich, profile_load
from summary import groups as summary_groups, table as summary_table
from summary.names import name_keys
from summary.personal import (DEFAULT_SHOW as PROFILE_SHOW, DEFAULT_VALUES as PROFILE_VALUES, DESCRIPTION as PROFILE_DESCRIPTION,
                              ISO_DATE, Profile, profile_fields)
from summary.recommendation import (DESCRIPTION as LETTER_DESCRIPTION, FILTERS as LETTER_FILTERS, HINTS as LETTER_HINTS,
                                    SOURCES as LETTER_SOURCES, Recommendation,
                                    range_label as letter_range_label)
from summary.transcript import DEFAULT_CATEGORY, DEFAULT_SOURCE, FILTERS as TRANSCRIPT_FILTERS, SOURCES as TRANSCRIPT_SOURCES, Transcript, range_label, source_hint, source_label
from summary.transcript import SOURCE_HINTS as TRANSCRIPT_HINTS, description as transcript_description
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


def _collect_pdfs(files, pdf_folder: Path, staging: Path) -> tuple[list[Path], str]:
    """Uploaded PDFs (saved into ``staging``), else every PDF in ``pdf_folder``."""
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
    folder = pdf_folder
    found = sorted(p for p in folder.glob("*.pdf") if p.is_file())
    if not found:
        raise ValueError(f"no PDFs found in {folder}")
    return found, str(folder)


def _find_pdf(name: str, cfg: dict) -> Path | None:
    """The original PDF behind a result, in <input-folder>/transcripts."""
    folder = folders.input_or_none(cfg, "transcripts")
    path = folder / name if folder else None
    return path if path and path.is_file() else None


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
    the Recommendations Upload page polls /letters/status meanwhile.  The ranks are merged
    into <work-folder>/letters/letter_ranks.csv when the run ends.
    """

    def start(self, client, pdfs: list[Path], work: Path, skipped: list[str]) -> bool:
        """Convert ``pdfs`` to <work>/<name>.txt, rank them into <work>/letter_ranks.csv."""
        return self._start((client, pdfs, work), done=0, total=len(pdfs), skipped=skipped)

    def _step(self) -> None:
        with self.lock:
            self.state["done"] += 1

    def _run(self, client, pdfs: list[Path], work: Path) -> None:
        ranks_csv = work / RANKS_CSV
        try:
            ranks, failures = rank_letters.rank_pdfs(client, pdfs, work, progress=self._step)
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
    """Every transcript PDF's name in <input-folder>/transcripts (as _find_pdf looks)."""
    folder = folders.input_or_none(cfg, "transcripts")
    return sorted(p.name for p in folder.glob("*.pdf")) if folder else []


def _letter_records(cfg: dict, transcripts: list[str]) -> tuple[list[dict], str | None]:
    """(student_info records for ``transcripts`` and every letter in <input-folder>/letters,
    ranked from <work-folder>/letters; an error message when the letters could not be read)."""
    folder = folders.input_or_none(cfg, "letters")
    try:
        return letter_students.folder_info(letters_folder(cfg), letters_work_folder(cfg), transcripts), None
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
             "letters": len(r["letters"]), "group_rank": r["group_rank"]}
            for r in records if r["letters"]], error


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


def _summary_rows(cfg: dict) -> list[dict]:
    """One row per student in the union of transcripts, letters and profiles.

    A student is the shared file-name key (adam_flores): <work-folder>/transcripts/<key>.common.json,
    <input-folder>/letters/<key>_letter<N>.pdf and <work-folder>/personal/<key>.json.  The
    name is the profile's "name", else the key title-cased."""
    work = folders.work_or_none(cfg, "transcripts")
    transcripts = {p.name[:-len(".common.json")] for p in work.glob("*.common.json")} if work else set()
    letters: dict[str, int] = {}
    inbox = folders.input_or_none(cfg, "letters")
    for pdf in inbox.glob("*.pdf") if inbox else ():
        letters[student_key(pdf)] = letters.get(student_key(pdf), 0) + 1
    profiles = profile_load.load_cfg(cfg)
    by_key = {Path(name).stem: data for name, data in profiles.items()}
    rows = []
    for key in sorted(transcripts | set(letters) | set(by_key)):
        name = by_key.get(key, {}).get("name")
        rows.append({"key": key,
                     "name": name if isinstance(name, str) and name.strip() else _student_name(key),
                     "profile": key in by_key, "transcript": key in transcripts,
                     "recommendations": letters.get(key, 0)})
    return rows


PROFILE_SUFFIX = ".json"  # Upload → Profiles reads these from <input-folder>/personal, saves <work-folder>/personal/<name>.json


def _student_profile(transcript: str, cfg: dict) -> list[list] | None:
    """The student dialog's Profile tab: [[field, value], ...] from
    <work-folder>/personal/<key>.json, the transcript's file key; None without one."""
    try:
        path = profile_load.personal_work_folder(cfg) / f"{Path(transcript).stem}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return [[k, v] for k, v in profile_display.fields(data).items()] if isinstance(data, dict) else None


def _profile_table(profiles: dict[str, dict]) -> tuple[list[str], list[tuple[str, dict]]]:
    """Column names (every key, in first-seen order) and one (file key, flat row)
    per profile.  The file name and id_* keys are not shown."""
    rows = [(Path(name).stem, profile_display.fields(data)) for name, data in profiles.items()]
    columns = list(dict.fromkeys(k for _, row in rows for k in row))
    return columns, rows


def _dialog_results(cfg: dict) -> tuple[str, str, Path | None]:
    """(builder, category, its <cat>_gpa.csv) for the student dialog's courses on
    a page without its own: the active builder's first category; "" without one."""
    try:
        builder, built = _builder_folder(cfg, None)
        available = _built_categories(built)
    except (category_builder.UnknownBuilder, KeyError):
        return "", "", None
    category = next(iter(available), "")
    return builder, category, available.get(category)


def create_app(config_path: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    config_path = Path(config_path or PROJECT_ROOT / "config.yaml")
    add_tool_folders(_load_config(config_path).get("tool-folders"))
    # Every page's column names: M.state and the like explain themselves on hover.
    app.jinja_env.globals.update(field_hint=profile_display.hint, profile_hints=profile_display.HINTS)

    @app.get("/")
    def welcome():
        return render_template("welcome.html", tab="welcome")

    # Transcripts FTP upload and category build share one page; the old
    # addresses lead there.
    @app.get("/transcripts")
    @app.get("/transcripts/category-build")
    def upload_redirect():
        return redirect("/transcripts/ftp-upload")

    @app.get("/transcripts/ftp-upload")
    def upload():
        cfg = _load_config(config_path)
        return render_template("upload.html", tab="ftp-upload",
                               pdf_folder=folders.input_or_none(cfg, "transcripts") or "",
                               work_folder=folders.work_or_none(cfg, "transcripts") or "")

    job = CategoryJob()

    def category_name(raw: str | None) -> str:
        name = (raw or "").strip().upper()
        if not CATEGORY_NAME.match(name):
            raise ValueError(CATEGORY_NAME_RULE)
        return name

    @app.get("/categories")
    def list_categories():
        """{name: definition} from the local definitions repo, with each one's origin
        (cloud: the shared text; local: this PC's own).

        ``names`` keeps the repo's order (jsonify sorts the object's keys).
        """
        try:
            definitions = Definitions(config_path)
            data = definitions.data
            cfg = _load_config(config_path)
            try:
                steps = definer.step_names(definer.load_config(cfg))
            except ValueError:
                steps = []  # a bad mode: the draft request reports it
            return jsonify(categories=data, names=list(data), path=str(definitions.folder),
                           origins={c: definitions.origin(c) for c in data},
                           builders=category_builder.names(),
                           active_builder=category_builder.active(cfg), definer=steps)
        except (OSError, yaml.YAMLError) as exc:
            return jsonify(error=f"could not read {config_path}: {exc}"), 500

    @app.post("/categories/draft")
    def draft_category():
        """A definition for a new category: the cloud's shared one when it has one
        (source "cloud"), else category-definer's draft. Nothing is saved."""
        try:
            name = category_name(request.form.get("name"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        definitions = Definitions(config_path)
        if name in definitions.data:
            return jsonify(error=f"{name} is already defined"), 409
        shared = definitions.cloud_definition(name)
        if shared is not None:
            return jsonify(name=name, definition=shared, source="cloud", attempts=[])
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
        """Save a definition into the local definitions repo.

        A new name (offered to the cloud, whose shared text wins; ``shared`` says
        created, adopted or pending) unless ``replace=1`` (Update, an existing one:
        a local override unless it equals the cloud's text).
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
            entry = definitions.update(name, definition) if replace \
                else definitions.publish(name, definition)
        except (OSError, ValueError) as exc:
            return jsonify(error=f"could not save to {definitions.folder}: {exc}"), 500
        log.info("category %s saved to %s (%s)", name, definitions.folder, entry["origin"])
        return jsonify(name=name, categories=definitions.data, origin=entry["origin"],
                       shared=entry.get("shared"), definition=entry["definition"])

    @app.post("/categories/use-cloud")
    def use_cloud_category():
        """Replace this PC's definition of a category with the cloud's shared one."""
        try:
            name = category_name(request.form.get("name"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        definitions = Definitions(config_path)
        entry = definitions.use_cloud(name)
        if entry is None:
            return jsonify(error=f"the cloud has no {name} definition (or cannot be reached)"), 404
        return jsonify(name=name, categories=definitions.data, origin="cloud",
                       definition=entry["definition"])

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
        """Convert the uploaded PDFs (saved in <input-folder>/transcripts), or every
        PDF already there, into <work-folder>/transcripts.

        The Upload tab posts here with fetch() and shows the answer under the
        form, so this returns a status, not a page.
        """
        cfg = _load_config(config_path)
        try:
            inbox = folders.input_folder(cfg, "transcripts")
            work = folders.work_folder(cfg, "transcripts")
        except KeyError as exc:
            return jsonify(ok=False, error=str(exc).strip("'\"")), 400
        staging = Path(tempfile.mkdtemp(prefix="browser-view-upload-"))
        try:
            try:
                work.mkdir(parents=True, exist_ok=True)
                pdfs, source = _collect_pdfs(request.files, inbox, staging)
            except (OSError, ValueError) as exc:
                return jsonify(ok=False, error=str(exc)), 400

            converted, failures = [], []
            for pdf in pdfs:
                try:
                    if pdf.parent == staging:
                        # An upload is input: keep it with the others so View can show it.
                        inbox.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(pdf, inbox / pdf.name)
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

    @app.get("/transcripts/view")
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

    @app.get("/transcripts/view/student")
    def student_detail():
        """One student's details for the student dialog: the transcript's PDF, its
        courses when a category is given, and the recommendation letters with
        their post_rank ranks, and the student's profile."""
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
        detail["pdf_url"] = f"/transcripts/view/pdf?{urlencode({'file': name})}" if pdf else None
        return jsonify({**detail, **_student_letters(name, cfg),
                        "profile": _student_profile(name, cfg)})

    @app.get("/transcripts/view/pdf")
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
        cfg = _load_config(config_path)
        return render_template("letters_upload.html", tab="letters-upload",
                               folder=folders.input_or_none(cfg, "letters") or "(input-folder not set)",
                               work=folders.work_or_none(cfg, "letters") or "(work-folder not set)")

    @app.post("/letters/upload")
    def run_letters_upload():
        """Save the uploaded letters in <input-folder>/letters (else take every PDF
        already there), then convert them into <work-folder>/letters and rank them
        in the background; /letters/status reports progress.

        A letter with a <name>.txt in the work folder was ranked before: it is
        skipped, not replaced."""
        cfg = _load_config(config_path)
        try:
            folder = letters_folder(cfg)
            work = letters_work_folder(cfg)
        except KeyError as exc:
            return jsonify(error=str(exc).strip("'\"")), 400
        try:
            client = rank_letters.jev_client(cfg)
            folder.mkdir(parents=True, exist_ok=True)
            work.mkdir(parents=True, exist_ok=True)
            done = {p.stem for p in work.glob("*.txt")}
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
        if not letter_job.start(client, sorted(pdfs), work, sorted(skipped)):
            return jsonify(error="letters are already being ranked", **letter_job.snapshot()), 409
        log.info("letters: ranking %d PDF(s) from %s into %s", len(pdfs), folder, work)
        return jsonify(letter_job.snapshot())

    @app.get("/letters/status")
    def letters_status():
        return jsonify(letter_job.snapshot())

    @app.get("/letters/view")
    def letters_view():
        cfg = _load_config(config_path)
        folder = folders.input_or_none(cfg, "letters") or "(input-folder not set)"
        # The dialog shows the transcript's courses from the active builder's first
        # category; its results also give the students' full names.
        names = {}
        builder, category, results = _dialog_results(cfg)
        try:
            if results:
                names = {r["file"]: r["student"] for r in _read_students(results, category)}
        except (OSError, ValueError, csv.Error):
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
        folder = folders.input_or_none(_load_config(config_path), "letters")
        pdf = folder / name if name and folder else None
        if pdf is None or not pdf.is_file():
            abort(404)
        return send_file(pdf, mimetype="application/pdf")

    @app.get("/profiles/upload")
    def profiles_upload():
        cfg = _load_config(config_path)
        return render_template("profiles_upload.html", tab="profiles-upload",
                               folder=folders.input_or_none(cfg, "personal") or "(input-folder not set)",
                               work=folders.work_or_none(cfg, "personal") or "(work-folder not set)")

    @app.post("/profiles/upload")
    def run_profiles_upload():
        """Read .json profile files, the uploaded ones (saved in <input-folder>/personal
        too) or, with none chosen, every *.json already there, and save each as
        <work-folder>/personal/<name>.json (replacing one of that name), which
        profile_load reads; then reload every profile into memory.

        Each input file must hold one JSON object; nothing is written unless every
        file is valid.  A profile without "institution" or "program" gets them
        from the transcript whose student.name is the profile's name (None if
        none is; profile/enrich.py); the input file itself is saved as it came."""
        cfg = _load_config(config_path)
        try:
            folder = profile_load.personal_folder(cfg)
            out = profile_load.personal_work_folder(cfg)
        except KeyError as exc:
            return jsonify(error=str(exc).strip("'\"")), 400
        uploads = [f for f in request.files.getlist("profiles") if f and f.filename]
        try:
            sources = []  # (input file name, its bytes, whether it is a new upload)
            if uploads:
                for upload in uploads:
                    name = Path(upload.filename).name  # never trust a client path
                    if not name.lower().endswith(PROFILE_SUFFIX):
                        raise ValueError(f"not a {PROFILE_SUFFIX} file: {name}")
                    sources.append((name, upload.read(), True))
            else:
                found = sorted(p for p in folder.glob(f"*{PROFILE_SUFFIX}") if p.is_file())
                if not found:
                    raise ValueError(f"no {PROFILE_SUFFIX} files found in {folder}")
                sources = [(p.name, p.read_bytes(), False) for p in found]
            parsed = []
            for name, data, _ in sources:
                try:
                    profile = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"{name}: not valid JSON ({exc})") from None
                if not isinstance(profile, dict):
                    raise ValueError(f"{name}: expected a JSON object")
                parsed.append(profile)
            fields = enrich.transcript_fields(folders.work_or_none(cfg, "transcripts"))
            folder.mkdir(parents=True, exist_ok=True)
            out.mkdir(parents=True, exist_ok=True)
            saved, filled, unmatched = [], {}, []
            for (name, data, uploaded), profile in zip(sources, parsed):
                if uploaded:
                    (folder / name).write_bytes(data)
                json_name = name[:-len(PROFILE_SUFFIX)] + ".json"
                added = enrich.add_missing(profile, fields)
                if added:
                    filled[json_name] = added
                    if not enrich.has_transcript(profile, fields):
                        unmatched.append(json_name)
                (out / json_name).write_text(
                    json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                saved.append(json_name)
            profiles = profile_load.load(out)
        except (OSError, ValueError) as exc:
            return jsonify(error=str(exc)), 400
        log.info("profiles: saved %d .json (filled in %d, no transcript for %d), "
                 "loaded %d from %s", len(saved), len(filled), len(unmatched), len(profiles), out)
        # filled: {file: attributes added}; unmatched: files whose added ones are empty
        return jsonify(status="done", saved=saved, loaded=len(profiles),
                       filled=filled, unmatched=unmatched,
                       errors=[f"{k}: {v}" for k, v in profile_load.ERRORS.items()])

    @app.get("/profiles/view")
    def profiles_view():
        cfg = _load_config(config_path)
        folder = folders.work_or_none(cfg, "personal") or "(work-folder not set)"
        profiles = profile_load.load_cfg(cfg)  # always what the folder holds now
        columns, rows = _profile_table(profiles)
        error = None if rows else f"no profiles in {folder} yet"
        # The student dialog shows courses from the active builder's first category.
        builder, category, _ = _dialog_results(_load_config(config_path))
        return render_template("profiles_view.html", tab="profiles-view", folder=folder,
                               columns=columns, rows=rows, error=error,
                               skipped=profile_load.ERRORS, builder=builder, category=category,
                               link_column="name" if "name" in columns else (columns or [""])[0])

    @app.get("/summary")
    def summary():
        rows = _summary_rows(_load_config(config_path))
        # (label, available, missing); a student has recommendations with one letter or more.
        counts = [(label, n, len(rows) - n) for label, n in (
            ("Profiles", sum(r["profile"] for r in rows)),
            ("Transcripts", sum(r["transcript"] for r in rows)),
            ("Recommendations", sum(r["recommendations"] > 0 for r in rows)))]
        return render_template("summary.html", tab="summary", rows=rows, counts=counts,
                               error=None if rows else "no transcripts, letters or profiles yet")

    def _ranges(filters: dict) -> dict[str, tuple[float | None, float | None]]:
        """{source: (low, high)} from ?<prefix>_min=&<prefix>_max= per source of
        ``filters`` ({source: (prefix, highest value or None)}): Transcript's
        gpa_, all_gpa_, pct_; Recommend Letter's score_, count_. A bound that is
        not a number from 0 to the source's highest value is no bound; a
        reversed range is swapped; a source with no bound is left out."""
        ranges = {}
        for source, (prefix, top) in filters.items():
            bounds = []
            for end in ("min", "max"):
                try:
                    value = float(request.args.get(f"{prefix}_{end}") or "")
                except ValueError:
                    value = None
                ok = value is not None and value >= 0 and (top is None or value <= top)
                bounds.append(value if ok else None)
            low, high = bounds
            if low is not None and high is not None and low > high:
                low, high = high, low
            if low is not None or high is not None:
                ranges[source] = (low, high)
        return ranges

    def _profile_query(fields: dict) -> dict:
        """Profile(...) keyword arguments from the query, for the known ``fields``
        (summary.personal.profile_fields): ?prof_show= (repeated, in order; none
        or no known one: institution and program, those the profiles have),
        ?prof_min.<field>=/?prof_max.<field>= (a range field: a number, or an
        ISO date for birth_date; a bad bound is no bound, a reversed range is
        swapped) and ?prof_in.<field>= (a list field's list[str]: one value per
        line, or repeated; blank lines and repeats, ignoring case, dropped; none
        given: summary.personal.DEFAULT_VALUES, sex M and F).
        Only a shown field filters: the others' filters are dropped."""
        show = [f for f in request.args.getlist("prof_show") if f in fields]
        if not show:
            show = [f for f in PROFILE_SHOW if f in fields] or list(fields)[:1] or list(PROFILE_SHOW)
        ranges, values = {}, {}
        for name, f in fields.items():
            if name not in show:
                continue
            if f.kind == "list":
                picked: dict[str, str] = {}
                for raw in request.args.getlist(f"prof_in.{name}"):
                    for line in raw.splitlines():
                        if line.strip():
                            picked.setdefault(line.strip().casefold(), line.strip())
                if picked:
                    values[name] = list(picked.values())
                elif name in PROFILE_VALUES:  # sex: M and F unless a list is given
                    values[name] = list(PROFILE_VALUES[name])
                continue
            bounds = []
            for end in ("min", "max"):
                raw = (request.args.get(f"prof_{end}.{name}") or "").strip()
                if f.kind == "date":
                    bounds.append(raw if ISO_DATE.fullmatch(raw) else None)
                    continue
                try:
                    bounds.append(float(raw))
                except ValueError:
                    bounds.append(None)
            low, high = bounds
            if low is not None and high is not None and low > high:
                low, high = high, low
            if low is not None or high is not None:
                ranges[name] = (low, high)
        return dict(show=show, ranges=ranges, values=values)

    def _profile_params(query: dict) -> list[tuple[str, str]]:
        """The applied Profile choice as (name, value) query params, for the other
        dialogs' hidden inputs."""
        params = [("prof_show", f) for f in query["show"]]
        for name, (low, high) in query["ranges"].items():
            for end, value in (("min", low), ("max", high)):
                if value is not None:
                    params.append((f"prof_{end}.{name}", value if isinstance(value, str) else f"{value:g}"))
        params += [(f"prof_in.{name}", v) for name, vs in query["values"].items() for v in vs]
        return params

    def _rank_objects(cfg: dict) -> dict:
        """Student Rank's objects as the query asks for them, and how they were
        picked: Transcript (from the active builder: ?category=, ?by=, its ranges;
        None when the builder has built nothing), Recommend Letter and Profile.
        Shared by the page and its AI box, so both see the same filters."""
        by = request.args.get("by")
        by = by if by in TRANSCRIPT_SOURCES else DEFAULT_SOURCE
        ranges = _ranges(TRANSCRIPT_FILTERS)
        rec_ranges = _ranges(LETTER_FILTERS)
        prof_fields = profile_fields(cfg)
        prof_query = _profile_query(prof_fields)
        out = dict(by=by, ranges=ranges, rec_ranges=rec_ranges, prof_fields=prof_fields,
                   prof_query=prof_query, profile=Profile(cfg, **prof_query),
                   builder=category_builder.active(cfg), categories=[], selected="",
                   transcript=None, error=None)
        # The Transcript column uses the active builder; its dialog picks the category.
        try:
            builder, folder = _builder_folder(cfg, None)
            available = list(_built_categories(folder))
        except (category_builder.UnknownBuilder, KeyError) as exc:
            out["error"] = str(exc).strip("'\"")
        else:
            selected = (request.args.get("category") or "").upper()
            if selected not in available:  # STEM when built, else the first category
                selected = DEFAULT_CATEGORY if DEFAULT_CATEGORY in available else next(iter(available), "")
            out.update(builder=builder, categories=available, selected=selected)
            if selected:
                out["transcript"] = Transcript(cfg, builder, selected, by, ranges, categories=available)
            else:
                out["error"] = (f"the {builder} builder has no results (*_gpa.csv) in {folder} yet,"
                                " so there is no Transcript column")
        out["recommendation"] = Recommendation(cfg, rec_ranges)
        out["objects"] = [o for o in (out["transcript"], out["recommendation"], out["profile"]) if o]
        return out

    @app.get("/view/rank")
    def view_summary():
        """One row per student name of every upload type, one column per ranked
        object: Transcript (three sub-columns, from the active builder: ?category=;
        ?by= category-gpa, all-courses-gpa or category-pct is the one sorted first; ?gpa_min=/?gpa_max=, ?all_gpa_min=/
        ?all_gpa_max= and ?pct_min=/?pct_max= keep only the students in those
        ranges), Recommend Letter (Score and Count; ?score_min=/?score_max= and
        ?count_min=/?count_max= keep only the students in those ranges) and
        Profile (one sub-column per ?prof_show= field, filtered: _profile_query)."""
        cfg = _load_config(config_path)
        rank = _rank_objects(cfg)
        by, ranges, rec_ranges = rank["by"], rank["ranges"], rank["rec_ranges"]
        prof_fields, prof_query, profile = rank["prof_fields"], rank["prof_query"], rank["profile"]
        transcript, recommendation = rank["transcript"], rank["recommendation"]
        page = dict(tab="view-summary", builder=rank["builder"], categories=rank["categories"],
                    selected=rank["selected"], by=by, sources={k: label for k, (_, label) in TRANSCRIPT_SOURCES.items()},
                    source_label=source_label, filters=TRANSCRIPT_FILTERS, ranges=ranges,
                    range_label=range_label, rec_ranges=rec_ranges, rec_filters=LETTER_FILTERS,
                    rec_sources=LETTER_SOURCES, rec_range_label=letter_range_label,
                    source_hint=source_hint, source_hints=TRANSCRIPT_HINTS, rec_hints=LETTER_HINTS,
                    prof_fields=prof_fields, prof=prof_query, prof_labels=profile.labels(),
                    prof_default_values=PROFILE_VALUES,
                    prof_params=_profile_params(prof_query),
                    names=[], keys={}, columns=[], groups=[], sort_col=1, valid=set(), error=rank["error"],
                    # each Config column's meaning: its hover text
                    descriptions={"Transcript": transcript_description(rank["selected"]),
                                  "Recommend Letter": LETTER_DESCRIPTION, "Profile": PROFILE_DESCRIPTION})
        objects = rank["objects"]
        try:
            keys = name_keys(cfg)  # name -> file key, for the student dialog
            names = list(keys)  # every name is a row (View's All)
            # each column applies only its own filter: a name it leaves out is – there only
            columns = summary_table(objects, names)
        except (KeyError, OSError, ValueError, csv.Error) as exc:
            page["error"] = f"could not build the summary: {exc}"
        else:
            # Valid: a name with a value in every column (View's All / Valid choice),
            # so one that passes every column's filter.
            valid = {n for n in names if columns and all(c.orders[n] is not None for c in columns)}
            # The page opens sorted by the Transcript's Rank by sub-column, else (no
            # Transcript column) by the Recommend Letter's Score, else by the first column.
            header = summary_groups(columns)
            want = ((transcript.title, source_label(by, page["selected"])) if page["selected"]
                    else (recommendation.title, LETTER_SOURCES["group-rank"]))
            sort_col = next((i for g in header for i, c in g.cols if (c.group, c.title) == want), 1)
            page.update(names=names, keys=keys, columns=columns, groups=header,
                        sort_col=sort_col, valid=valid)
            if not names:
                page["error"] = page["error"] or "no transcripts, letters or profiles yet"
        return render_template("view_summary.html", **page)

    ask_lock = threading.Lock()  # one AI request at a time: one local model, one browser profile

    @app.post("/view/rank/ask")
    def view_summary_ask():
        """Config → Ask AI: the request (form or JSON "question") turned into
        Student Rank filters by rank_ai, starting from the filters in this URL's
        query (the page's own). JSON: url (the page with the new filters),
        explanation, unsupported, problems, filters, source, attempts; or error."""
        data = request.get_json(silent=True) or request.form
        question = str(data.get("question") or "").strip()
        if not question:
            return jsonify(error="type a request first"), 400
        if not ask_lock.acquire(blocking=False):
            return jsonify(error="another request is being answered; try again when it is done"), 409
        try:
            cfg = _load_config(config_path)
            objects = _rank_objects(cfg)["objects"]
            result = rank_ai.ask(cfg, question, objects)
        except ValueError as exc:  # a bad rank-ai: section
            return jsonify(error=str(exc)), 400
        except rank_ai.AskFailed as exc:
            return jsonify(error=str(exc), attempts=[a.line() for a in exc.attempts]), 502
        finally:
            ask_lock.release()
        return jsonify(url=f"/view/rank?{urlencode(result.params)}", **result.to_dict())

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


def _cloud_token_notice(config_path: Path) -> None:
    """At start, in cloud mode: pick up an approved token (jev_category.enroll), or print
    the notice that this laptop has none yet.  The UI still starts either way."""
    from jev_category import builder as jev_builder

    try:
        cfg = jev_builder.jev_settings(yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {})
        if cfg["mode"] == "cloud" and cfg["cloud_url"]:
            jev_builder.cloud_token(cfg)
    except RunError as exc:
        print(f"NOTICE: {exc}")
    except (OSError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python3 -m browser_view", description=__doc__.split("\n")[0])
    parser.add_argument("-c", "--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost)")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    log_path = setup_logging(_load_config(args.config))
    _cloud_token_notice(args.config)
    app = create_app(args.config)
    print(f"Browser View on http://{args.host}:{args.port} (log: {log_path})")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0
