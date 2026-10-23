# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Everything runs from the repo root with the project venv (`.venv312`, Python 3.12). Packages are `-m` modules, not scripts.

```bash
.venv312/bin/python -m pytest tests/ -q                                   # all tests
.venv312/bin/python -m pytest tests/test_upload.py -q                     # one file
.venv312/bin/python -m pytest tests/test_upload.py::test_add_gpa_uses_the_key_and_skips_pass_fail

.venv312/bin/python -m upload                                             # PDFs in pdf-folder -> work-folder
.venv312/bin/python -m category_builder --category STEM [--builder llm] [--publish]
.venv312/bin/python -m llm_category --category STEM --limit 3             # one builder directly
.venv312/bin/python -m out_builder --category STEM [--builder llm]       # publish CSV to out-folder
.venv312/bin/python -m category_definition_builder STEM --only chatgpt    # one definer step by hand, saves nothing
.venv312/bin/python -m browser_view [--debug]                             # Flask UI on 127.0.0.1:5001
```

Tests need no Ollama, pdftotext, browser, or TypeSafe API. All of them are faked, and `tests/conftest.py` puts the repo root and `install/` on `sys.path`. There is no linter or build step.

Setting up a machine (venv, pdftotext, Ollama models, `config.yaml`) is done by the `/install-gpa-tools` skill (`.claude/skills/install-gpa-tools/`), which uses the scripts in `install/`.

## Architecture

The pipeline computes a per-student **category GPA** (STEM, MEDICAL, ...) from transcript PDFs:

```
PDF -> upload -> <work>/<name>.common.json -> category builder -> <work>/<builder>/<cat>_gpa.csv + <cat>_courses.csv -> out_builder -> <out>/<cat>_gpa.csv
```

- **`config.yaml` is the single source of settings.** It holds the folders, the builder settings, the definer settings and the category definitions, and it is tracked in git. Every package reads it, and `browser_view` writes to it. `category_builder/definitions.py` rewrites only the `category-definitions:` section and leaves the rest of the file, comments included, byte for byte. Keep that guarantee if you touch config writing. The API key is never stored in it: `api-key-env` names the environment variable that holds it.
- **upload** turns `pdftotext -layout` output into the common transcript JSON. `upload/common/txt2common.py` parses with regexes plus `labels.json` and `template.json`, so every output has the template's keys. `add_gpa.py` then adds `gpa` (the school's own scale) and `gpa_4` (the common 0–4 scale from `standard_scale.json`) to each course. The GPA source is chosen in this order: optional `school_rules/<slug>.json`, then the transcript's grading key, then points/credits. Mismatches go into `warnings` and are never raised as errors. There is no OCR.
- **category_builder** is a registry and has no category logic of its own. `BUILDERS` in `category_builder/__init__.py` maps a name to a module that provides `load_config(config_path)` (which returns at least `folder`), `run(cfg, category, progress=None)` and an optional `estimate(...)`. Builders write their CSVs through `category_builder.common.write_outputs` and keep their caches in `<work>/<builder>/`. To add a builder, add one line to `BUILDERS` and a `category-builder: <name>:` section to config.yaml; the UI then lists it automatically. Builders only produce data: there is no ranking column, and no student is dropped.
  - `llm_category`: Ollama answers yes/no for each distinct course (code + title), at temperature 0 with schema-constrained output. The cache `cache_<cat>.json` is stamped with a hash of `category-build-model` plus the definition. Changing either one throws away all cached answers, which means hours of re-querying on CPU.
  - `jev_category`: the TypeSafe API scores each course 0–1, sent in batches, and a course counts as in the category when its score is above `threshold`.
- **Category definitions are shared by all builders.** When a category has no definition, `category_builder/definer.py` drafts one through `category_definition_builder`, which uses Playwright to drive the installed Chrome/Edge against the free ChatGPT and Gemini web chats with no account. The modes are `dev` (ChatGPT → Gemini → Ollama, in visible windows) and `prod` (headless Gemini → Ollama). `pipeline.py` runs each browser attempt in its own OS process with a hard deadline, because Playwright's sync API can't share a thread with Flask's request threads. `client.py` parses the reply's `DEFINITION: ... END` block. Site selectors live in `browser_legs.py` and break when the sites change; the chain then falls through to the next step.
- **browser_view** (`browser_view/app.py`, Flask with Jinja templates): the Upload tab runs upload, the definer and builds as background `CategoryJob`s, one at a time, and publishes the result after each build. The View tab reads `<work>/<builder>/<cat>_gpa.csv` and `_courses.csv` and serves the transcript PDFs. It is meant as a localhost tool for a single operator.
- Logs go to `<log-folder>/` as `upload.log`, `llm_category.log`, `jev_category.log`, `category_definer.log` and the browser_view log.

`README.md` documents the user-facing behavior in detail (config keys, UI flows, model sizing). Keep it in sync when behavior changes.
