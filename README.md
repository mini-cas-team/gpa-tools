# gpa-tools

Compare students by average **category GPA** (STEM, MEDICAL, ...) read out of
student transcript PDFs. A category builder decides which courses belong to a
category: `llm`, a local LLM (Ollama), or `jev`, TypeSafe's hosted model. A
category's definition is drafted by `category-definer`, through ChatGPT's or
Gemini's free web chat or the local LLM, and then edited by you.

```
PDF ──► upload ──► work/<name>.common.json ──► category builder ──► work/<builder>/<cat>_gpa.csv ──► out_builder ──► out/<cat>_gpa.csv
        pdftotext   (gpa_4 per course)         llm: Ollama yes/no     work/<builder>/<cat>_courses.csv   (copy)          (public output)
                                               jev: TypeSafe 0-1 score
                                                    ▲
                        category-definitions ───────┘  (drafted by category-definer: chatgpt / gemini / ollama)

browser_view: the Upload tab drives all three; the View tab reads work/<builder>/
```

```bash
.venv/bin/python -m browser_view      # http://127.0.0.1:5001
```

## Getting started

```bash
git clone https://github.com/johnprofessional132/gpa-tools.git
cd gpa-tools
claude                     # then type:  /install-from-clone, then /install-gpa-tools
```

`/install-from-clone` installs the code: it finds the Pythons on the machine,
builds `.venv` with the best supported one (3.14, else 3.12, else uv fetches
3.14 for you) and runs the tests. Without Claude Code:
`python3 install/setup_venv.py --dry-run` shows the plan, and without
`--dry-run` it does it.

The `/install-gpa-tools` skill does the rest in your own user account, with no
admin rights, on Linux, macOS or Windows:
- the venv (Python 3.14 or 3.12) and packages (Playwright among them; it drives the installed Chrome or Edge)
- pdftotext (into `.tools/`, if it's missing)
- Ollama and two qwen2.5 models sized to the machine: a fast one for building (fits in the GPU, if there is one) and the largest that fits for drafting definitions
- `config.yaml`: folders, the two models and the Ollama URL, the definer's mode and,
  optionally, the jev builder, set by `install/configure.py`

It checks what is already installed first and asks before downloading. See
[install/README.md](install/README.md) for the scripts it uses, which also
work by hand. `config.yaml` is tracked in git.

gpa-tools runs on **Python 3.14 or 3.12** (3.14 preferred; 3.13 and 3.11 are not
supported). `tests/test_python_versions.py` keeps the code 3.12-safe while you
work on 3.14: no newer syntax or standard-library modules without a fallback, and
no annotation that 3.12 could not evaluate on import.

Or by hand, on Linux with admin rights:

```bash
python3.14 -m venv .venv                        # or python3.12
.venv/bin/pip install -r requirements.txt
sudo apt install poppler-utils                  # pdftotext
curl -fsSL https://ollama.com/install.sh | sh   # Ollama
ollama pull qwen2.5:3b                          # build model
ollama pull qwen2.5:7b                          # definition model
.venv/bin/python install/configure.py --data ~/gpa-tools-data \
    --model qwen2.5:3b --definer-model qwen2.5:7b
```

## Usage

### Settings

```yaml
# config.yaml
components: [transcripts, letters, personal]
input-folder: /home/personal/Templates             # raw input, <input-folder>/<component>/; never written by a run
work-folder: /home/personal/Templates/work         # made from it, <work-folder>/<component>/; and <builder>/ folders
out-folder: /home/personal/Templates/out           # published <cat>_gpa.csv, for other applications
log-folder: /home/personal/Templates/log           # upload, llm_category, jev_category, category_definer, browser_view .log
min-category-courses: 4      # View tab: hide students with fewer category courses
run_mode: dev                # dev: the student dialog's letters tab also shows Jev's rank and confidence

active-category-builder: jev # the default in the Upload and View tabs and the CLI
category-builder:            # one section per builder
  llm:
    category-build-model: qwen2.5:3b       # the llm builder's yes/no answers: the fast one
    category-definition-model: qwen2.5:7b  # category-definer's ollama step: as large as fits
    ollama-url: http://localhost:11434     # both models: any in `ollama list` there
  jev:
    model: jev-1.14.0        # a version pins it; `jev-latest` follows new releases
    api-url: https://api.typesafe.ai/v1/systemone
    api-key-env: GPA_TOOLS_KEY   # env var holding the TypeSafe API key; never the key itself
    threshold: 0.5           # in the category when Jev's score is above this
    batch-size: 20           # courses per request

category-definer:            # drafts definitions: the mode's steps in turn until one gives one
  mode: dev                  # dev: chatgpt -> gemini -> ollama (browser windows); prod: gemini headless -> ollama
  retries: 1                 # extra tries for chatgpt and gemini
  browser-timeout: 120       # seconds to wait for a site's reply
  browser: auto              # Chrome, else Edge, else Playwright's Chromium; or chrome / msedge / chromium

category-definitions:        # shared by every builder; drafted by category-definer, edit freely
  STEM: STEM stands for Science, Technology, Engineering, and Mathematics. ...
```

Each component has an input folder, which holds only what you supply, and a
work folder, which holds only what the tools make from it (`folders.py`):

| component | `<input-folder>/<component>/` | `<work-folder>/<component>/` |
|---|---|---|
| `transcripts` | `<name>.pdf` | `<name>.txt`, `<name>.common.json` |
| `letters` | `<key>_letter<N>.pdf` | `<key>_letter<N>.txt`, `letter_ranks.csv` |
| `personal` | `<name>.profile` | `<name>.json` |

Everything in a work folder can be made again from the input, so deleting it
only costs a rerun. The builders keep their CSVs and caches in
`<work-folder>/<builder>/` (`jev/`, `llm/`), and category-definer its browser
profiles in `<work-folder>/browser-profiles/`. Both `input-folder` and
`work-folder` must be set: there is no default. A component left out of
`components` is turned off (its pages say so) rather than renamed.

### 1. upload: PDF to common JSON

```bash
.venv/bin/python -m upload            # every PDF in <input-folder>/transcripts
```

For each PDF it writes two files into `<work-folder>/transcripts`:

- `<name>.txt`, from `pdftotext -layout`.
- `<name>.common.json`, the common transcript format from
  `upload/common/txt2common.py`, with `gpa` and `gpa_4` added per course by
  `add_gpa.py`. This is what every category builder reads.

There is no OCR here: a scanned PDF without a text layer is reported and
skipped. Files with the same name are replaced.

### 2. Category builders: which courses are in the category

```bash
.venv/bin/python -m category_builder --category STEM              # the active builder
.venv/bin/python -m category_builder --category STEM --builder llm --publish
.venv/bin/python -m llm_category --category STEM --limit 3       # llm only, 3 students
.venv/bin/python -m jev_category --category STEM                 # jev only
```

A builder reads every `*.common.json` in `<work-folder>/transcripts` and writes into its own
folder, `<work-folder>/<builder>/`:

| file | contents |
|---|---|
| `<cat>_gpa.csv` | one row per student: category GPA, all-courses GPA, category and total course counts, category share |
| `<cat>_courses.csv` | every counted course with its `is_<cat>` flag, and `<cat>_score` (0–1) from builders that score (jev) |
| anything else | the builder's own working files (llm, jev: `cache_<cat>.json`) |

Builders provide data, not a ranking: there is no rank column and no one is
left out. Rank by sorting the View tab's columns, or however the application
reading the CSV wants.

**Definitions** are shared by every builder and kept in
`category-definitions`. When a category has none, `category-definer` drafts
one as a starting point and saves it; edit it before (re)building. Drafting
is separate from building, so it uses its own sources: the web chats below,
then the local `category-definition-model`.

The definer's `mode` picks its steps. It moves on whenever a step fails or
its reply has no usable definition, and ChatGPT and Gemini get one retry each
(`retries`):

| mode | steps | needs |
|---|---|---|
| `dev` (default) | ChatGPT → Gemini → Ollama, each site in a visible browser window | a desktop session |
| `prod` | Gemini headless (no window) → Ollama | nothing on screen; fine for a server |

Ollama here is `category-definition-model`. Neither mode needs an API key.

ChatGPT is only in `dev` because it stops a headless browser at its bot check.
The two sites are used through their free web chats, anonymously, with no
account and no API key: `category_definition_builder` has Playwright drive the
installed Chrome (else Edge, else Playwright's own Chromium, `browser: auto`),
type the question into chatgpt.com or gemini.google.com and read the reply.
No browser extension is needed. Each try is logged to
`<log-folder>/category_definer.log` and shown in the Upload tab, failures
included. To check one step by hand (nothing is saved):

```bash
.venv/bin/python -m category_definition_builder STEM --only chatgpt
.venv/bin/python -m category_definition_builder STEM --mode prod --only gemini
```

The sites' page layouts are not an API: when one changes, that step fails
and the next takes over, until the selectors in
`category_definition_builder/browser_legs.py` are updated.

**Adding a builder** (e.g. `mine`): write a module with `load_config(config_path)`
(returning at least `folder`), `run(cfg, category, progress=None)` and
optionally `estimate(cfg, category, definition)`; have it write its CSVs with
`category_builder.common.write_outputs`; add one line to `BUILDERS` in
`category_builder/__init__.py` and a `category-builder: mine:` section to
config.yaml. The Upload and View tabs list it from then on.

#### The llm builder and its two models

Needs [Ollama](https://ollama.com) running locally with both models of
`category-builder: llm:` pulled:

| key | used for | picked to be |
|---|---|---|
| `category-build-model` | this builder's yes/no answer per course, ~400 a run | **fast**: the biggest that fits in GPU memory (up to 14b), 3b without a GPU |
| `category-definition-model` | category-definer's last step, one call per category | **as large as fits**: GPU memory plus 70% of RAM, up to 14b without a GPU |

The installer picks both from the hardware (`install/check.py`); on a 12 GB
laptop without a GPU that is 3b and 7b. Change either with
`install/configure.py --model <build> --definer-model <definition>`.

For every distinct course (code + title) across
all students it asks the LLM a yes/no question: "using only that definition,
is this course in the category?" Answers are constrained to `yes`/`no` by a
JSON schema, at temperature 0, so the same question gets the same answer.

**The cache, `<work-folder>/llm/cache_<cat>.json`, is what makes reruns
fast.** It is keyed by course and stamped with a hash of
`category-build-model` and the definition (the definition model is not part
of it):

- Same definition and category-build-model: every saved answer is reused, no LLM calls, and
  the two CSVs are rebuilt in seconds from the current `.common.json` files.
- Edited definition or different category-build-model: the saved answers are discarded and
  every distinct course is asked again. That is roughly 400 courses for 64
  students, at about 13–16 s each on a CPU-only laptop, so 1.5–2 hours.
- New students: only courses not seen before are asked.
- Answers are saved one by one, so an interrupted run resumes where it stopped.
- The prompt wording in `llm_category/llm.py` is **not** part of the stamp.
  After changing a prompt, delete `cache_<cat>.json` by hand.

`llm_category/archive/definitions.yaml` keeps earlier definitions, such as
STEM, for restoring later.

#### The jev builder

Uses [TypeSafe](https://docs.typesafe.ai)'s hosted Jev model. Create an API
key in the TypeSafe console (API Keys) and export it in the variable that
`api-key-env` names, e.g. `export GPA_TOOLS_KEY=...` in `~/.bashrc`; the key
never goes in config.yaml, which is in git.

For every distinct course it asks one Noul question ("using only
`definition`, is the course in the category?") and gets back the probability
of yes. `batch-size` courses go in one request, answered in parallel; all
~400 STEM courses take a few seconds and cost well under a cent (input tokens
only; the run logs the count). A course is in the category when its score is
above `threshold`, and the score is kept in `<cat>_courses.csv`: scores near
the threshold are the borderline courses worth a look, and usually mean the
definition should say which side they are on.

The cache, `<work-folder>/jev/cache_<cat>.json`, works like the llm builder's
(stamped with model and definition) but stores scores, so changing
`threshold` needs no new API calls. The model is pinned to a version so
cached scores and new ones come from the same model; change it on purpose.

### 3. out_builder: publish to out-folder

```bash
.venv/bin/python -m out_builder --category STEM [--builder llm]
```

Copies `<work-folder>/<builder>/<cat>_gpa.csv` to `<out-folder>/<cat>_gpa.csv`,
the public output for other applications. The name carries no builder:
out-folder holds whichever builder was published last for that category. The
Upload tab publishes after every run it starts; on the command line, a build
publishes only with `--publish` or this command.

### 4. browser_view: the web UI

```bash
.venv/bin/python -m browser_view      # http://127.0.0.1:5001
```

| flag | |
|---|---|
| `-c PATH` | config file (default `config.yaml`) |
| `--host` / `--port` | bind address, default `127.0.0.1:5001` |
| `--debug` | Flask reloader and tracebacks |

`/` is a welcome page that links every page. Pages have no title bar, except
Upload Summary and Student Rank, whose title leads back to it. A left bar lists
the pages in two groups.
**Upload**: *Summary* (`/summary`), *Transcripts* (`/transcripts/ftp-upload`,
the Upload tab below), *Recommendations* (`/letters/ftp-upload`) and *Profiles*
(`/profiles/upload`). **View**: *Rank* (`/view/summary`), *Transcripts*
(`/transcripts/view`), *Recommendations* (`/letters/view`) and *Profiles*
(`/profiles/view`).
`/transcripts` and the old `/transcripts/category-build` redirect to
`/transcripts/ftp-upload`.

**Upload tab** (Upload > Transcripts). Two panels side by side, each with
its own scroll bar.

- *Left, Transcripts Upload:* choose PDFs to upload, or leave it empty to convert every PDF
  in `<input-folder>/transcripts`. Uploaded PDFs are saved there (they are input),
  so View can open them. Each becomes `.txt` and `.common.json` in
  `<work-folder>/transcripts`, as with `python -m upload`.
- *Right, Category Build:* an action bar of **Builder** and **Category** (dropdowns),
  **Update** and **Add**. Builder starts on `active-category-builder`. With no
  `category-definitions`, only Add is shown.
  - The dropdown shows the selected category's definition, read-only. It
    starts on the first one in `config.yaml`.
  - **Update** makes the definition editable, with **Go** and **Cancel**. Go
    saves the text to `config.yaml` and runs the selected builder in the
    background, with a progress bar and time left, then publishes the result
    to out-folder. When it finishes, the panel shows the definition the run
    used and an *Open in View* link.
  - **Add** shows a **Name** box with **Go** and **Cancel**. Go asks
    category-definer for a draft (about a minute through ChatGPT or Gemini,
    longer when it falls back; the progress line names the steps), saves it
    to `config.yaml`, and switches to Update so you can edit it before
    building. The result names who drafted it, and failed tries are listed
    under it.
  - While Update or Add is active, the other button and the dropdown are
    disabled. One run at a time is allowed.

**View tab.** Pick a **Builder** and a **Category**; it reads
`<work-folder>/<builder>/<cat>_gpa.csv` and shows every student. Click a column
name to sort (it opens sorted by category GPA). **Min courses** hides students
with fewer category courses (default `min-category-courses`). **Reload**
re-reads the folder, e.g. after a command-line run. Clicking a row highlights
it; clicking a student's name opens their details in three tabs: *Transcript*, their courses from `<cat>_courses.csv`
next to the transcript PDF, *Recommendation letters*, each letter with its
post_rank rank next to the selected letter's PDF, and *Profile*, the student's
`<work-folder>/personal/<key>.json` (matched by the transcript's file key) as a
table of field and value, flattened as on View → Profiles.
The dialog's header gives the student's **recommend letter**, the group rank of
their recommendation letters (`letter_builder/students.py`, `student_info`): letters
`<student>_letter<N>.pdf` in `<input-folder>/letters` match transcript `<student>.pdf`,
and their post_rank ranks combine into a score: 100 (very excellent), 90
(excellent), 80 (very good), 70 (good) or 60 (normal); `–` when the student has
no ranked letters.

**Recommendations, Upload.** Choose letter PDFs, or leave it empty to take
every PDF in `<input-folder>/letters`. A letter whose `<name>.txt` is already in
`<work-folder>/letters` was ranked before and is skipped (not saved, not ranked
again). New ones are saved in `<input-folder>/letters`; then, in the background
with a progress bar, each becomes `<work-folder>/letters/<name>.txt` and Jev ranks it
*excellent*, *good* or *normal* (one `choice` question per letter, using the
`category-builder: jev:` model, URL and key). The ranks are merged into
`<work-folder>/letters/letter_ranks.csv` (`filename,rank,confidence`), replacing
rows for the same file. One run at a time.

**Recommendations, View.** One row per student with letters in
`<input-folder>/letters`: **Student**, the number of **Letters**, and the **Group rank**
(100, 90, 80, 70 or 60, as above) from `student_info` (`letter_builder/students.py`), which combines the
student's letter ranks after `post_rank.py` (*good* below 0.7 becomes
*normal*; *excellent* below 0.4 becomes *normal*, and below 0.7 *good*;
`letter_ranks.csv` keeps Jev's own answers). Best group rank first; filter by
**Group rank**, sort by column. Clicking a row highlights it; the student's name
links to the same student dialog as Transcripts → View, with the transcript's courses from the active
builder's first category.

The same steps run from the command line:

```bash
.venv/bin/python -m letter_builder.pdf2txt        # <input-folder>/letters/*.pdf -> <work-folder>/letters/*.txt
.venv/bin/python -m letter_builder.rank_letters   # those .txt -> letter_ranks.csv beside them (rewritten)
.venv/bin/python -m letter_builder.post_rank      # print the adjusted ranks
```

**Profiles.** `profile/profile_load.py` reads every `*.json` in
`<work-folder>/personal` into memory: `profile_load.PROFILES` is a dict keyed by file
name (`adam_flores.json` → its JSON object). `load()` re-reads the folder and
replaces the dict; a file that is not valid JSON, or not a JSON object, is left
out and listed in `profile_load.ERRORS`. *Upload → Profiles* reads `.profile` files (each one JSON object): the chosen
ones, which are saved in `<input-folder>/personal`, or with none chosen every
`*.profile` already there. Each is saved as `<work-folder>/personal/<name>.json`, replacing
one of that name; nothing is written if any file is not a JSON object. A
profile without an `institution` or `program` attribute gets it in its
`.json` (`profile/enrich.py`, `FIELDS`), from the transcript whose
`student.name` equals the profile's `name` (exact match): `institution.name`
and `student.major` (the program, as the builders call it), or `null` when no
transcript has that name. An attribute the profile has, even `null`, is kept. The `.profile` is not changed. Then it
reloads. *View → Profiles* reloads and shows one row per file,
with nested fields as `parent.field` columns, sortable and searchable. Clicking
a row highlights it; the **name** links to the student dialog (as on
Transcripts → View), matched by the profile's file key. Each
residence becomes three columns, its `residence_country`, `residence_state` and
`residence_city`: `M.country`, `M.state`, `M.city` for `major_residence` and
`S.country`, `S.state`, `S.city` for `second_residence` (all `–` when there is
none). Hovering a column name explains what the field means (e.g. `M.state`:
the state the applicant mainly lives in), here, in the student dialog's Profile
tab and on Student Rank. The file name and `id_*` fields are
not shown.

```bash
.venv/bin/python -m profile.profile_load          # load <work-folder>/personal, print the count
```

The package is named `profile`, so it shadows the standard library's `profile`
(and `cProfile`) whenever the repo root is on `sys.path`.

**Summary** (`/summary`, under Upload). One row per student in the union of
transcripts, letters and profiles, matched on the shared file-name key
(`adam_flores`): **Name** (the profile's `name`, else the key title-cased),
**Profile** (`<work-folder>/personal/<key>.json` exists), **Transcript**
(`<work-folder>/transcripts/<key>.common.json` exists, i.e. uploaded and converted) and
**Recommendations** (the number of `<key>_letter<N>.pdf` in `<input-folder>/letters`).
The page's heading is *Upload Summary*. Above the student table, a counts
table gives, for profiles, transcripts and
recommendations, the number of **Students** (the total), how many have one (**Available**; for
recommendations, at least one letter) and how many do not (**Missing**).
Below it, **Show** filters the student table to those missing something, or
to complete ones, with the number of rows it shows (**Total**) beside it.

**View → Rank** (`/view/summary`, headed *Student Rank*). The page uses the
whole window width, in two panels: *Config* on the right is as narrow as its
table, and *View* on the left takes the rest.

*View* is a table of N+1 columns (after its heading, an **All / Valid**
dropdown, then the number of rows shown: All shows every row, Valid only the
rows with a value in every column; the browser remembers the choice): **Name**, then one column per ranked object.
An object can instead head several sub-columns over a second header row by
overriding `columns()` (each becomes a `Column` with `group` set;
`summary.groups(columns)` builds the header): **Transcript** has three,
*STEM GPA*, *ALL GPA* and *STEM CRS %* (named for the chosen category), and
**Recommend Letter** has two, *Score* and *Count*.
Each group header's arrow, drawn like the sort arrows, folds it: **▲** shows
the sub-columns; click it and **▼** shows one column, *STEM/ALL/%* or
*Score/Count* (`ABS.folded_title`), whose cells are the values as
`3.50/3.20/40.00` or `90/2`; it has no sort of its own (the rows keep their
order; unfold to sort). The browser remembers each group's choice.
Each object is a subclass of `summary.ABS` whose `rank()` returns a list of
`RankItem(name, order)`. **Name** is the union of the student names of every
upload type (`summary.names.upload_names`): transcripts' `student.name`,
profiles' `name`, and for letters (`summary.names.letter_names`) the name of the
transcript or profile with the same file key, else the key title-cased. Each
cell shows the object's `order` for that name, matched exactly: `–` (None) when
its `rank()` has no item with that name; items with a name outside the union
are dropped. `summary.table(objects, names)` builds the columns. Decimal values
show two decimals; whole numbers show as they are. The table opens sorted by
the Transcript's **Rank by** sub-column (without a Transcript column, the
Recommend Letter's Score; else the first column), highest first;
every column header, sub-columns included, sorts on click; a blank (`–`) is the lowest value, so it is last high-to-low and first low-to-high. Clicking a row highlights it; each **Name**
links to the student dialog, by the name's file key (`summary.names.name_keys`),
with the Transcript column's category.

Every heading explains itself on hover: a column by its meaning (*Profile*:
the applicant's personal information), each sub-column by what its value is
(*STEM GPA*: GPA 0–4 of the applicant's STEM courses), also in the dialogs.

*Config* is a table of **Column** and its **Filter** (one line per filter in
use); clicking a column opens its dialog, and **Apply** rebuilds the table.
A filter applies only to its own sub-column: a value outside it shows `–` in
that cell, and every other cell keeps its value; no student is ever dropped. View → *All*
lists every name; *Valid* lists only the students with a value in every column,
so only those that pass every filter. Each
dialog keeps the other columns' choices. The Transcript's Rank by is set in its
dialog only.

| Column | Object | Dialog | Rank by |
|---|---|---|---|
| *Transcript* | `summary.transcript.Transcript`: every `<work-folder>/*.common.json`, its three values (one sub-column each) from the active builder's (`active-category-builder`) `<cat>_gpa.csv`, matched by the transcript's file | **Category** (`?category=`, default STEM, else the builder's first), **Rank by** (`?by=`, the sub-column the table opens sorted by) and **Filter**, a from–to range per source, both ends included, either may be empty: *STEM GPA* (`?gpa_min=`/`?gpa_max=`, 0–4), *ALL GPA* (`?all_gpa_min=`/`?all_gpa_max=`, 0–4) and *STEM CRS %* (`?pct_min=`/`?pct_max=`, 0–100), whatever Rank by is. A range never drops a student: a value outside it (or none) shows `–` in that sub-column only, and the other sub-columns keep their values. Config's **Filter** column lists the ranges in use | `category-gpa`: *STEM GPA* (`<CAT>_GPA`, the default); `all-courses-gpa`: *ALL GPA* (`all_courses_GPA`); `category-pct`: *STEM CRS %* (`<cat>_all_pct`) |
| *Recommend Letter* | `summary.recommendation.Recommendation`: every student with a letter PDF in `<input-folder>/letters`, ranked from `<work-folder>/letters/letter_ranks.csv` after post_rank; two sub-columns, *Score* (100, 90, 80, 70 or 60, the group rank of Recommendations → View; `–` until a letter is ranked) and *Count* (how many letters they have) | **Filter**, a from–to range each, both ends included, either may be empty: *Score* (`?score_min=`/`?score_max=`, 0–100) and *Count* (`?count_min=`/`?count_max=`, 0 or more). A range never drops a student: a value outside it (or no Score yet) shows `–` in that sub-column only. Config's **Filter** column lists the ranges in use | – (no Rank by; without a Transcript column the table opens sorted by *Score*) |
| *Profile* | `summary.personal.Profile`: every `<work-folder>/personal/*.json` with a `name`, its fields as View → Profiles shows them (`profile/display.py`); one sub-column per shown field | A table of every field but `name`, one row each: **Show** (`?prof_show=`, repeated; the sub-columns, in the rows' order; default `institution` and `program`, those the profiles have, else the first field; at least one), **Field** and **Filter**. A range field, `age`, `birth_date` (ISO dates) and `years_of_military_service`, has from–to (`?prof_min.<field>=`/`?prof_max.<field>=`, both ends included, either may be empty). Every other field is a string list, a `list[str]` typed one value per line (`?prof_in.<field>=`, newline-separated or repeated): a value in the list, ignoring case, is shown, any other (or none) shows `–`; an empty list keeps every value. `sex` starts as `M` and `F` (`summary.personal.DEFAULT_VALUES`), and goes back to them when its box is emptied. Only a shown field filters: while **Show** is not ticked, its filter is turned off in the dialog, and the server drops it. A filter never drops a student or blanks another field: only its own field's cells outside it show `–` | – (no Rank by: `rank()` gives the first shown field; a text field sorts as text) |

With no category built yet there is no Transcript column. To add a column,
write another `ABS` subclass, add it to the list in the `/view/summary` route,
and give it a Config row and dialog in `view_summary.html`.

**This is a localhost tool for one operator.** It serves files from the
configured folders, and Flask's development server is what `--host` binds;
neither belongs on a network without authentication in front of it.

## Layout

```
config.yaml       folders, builders, definer, category definitions
folders.py        input-folder / work-folder per component (transcripts, letters, personal)
upload/           PDF -> .txt + .common.json (python -m upload)
  common/         txt2common, add_gpa, label and grade-scale tables
category_builder/ builder registry and CLI (python -m category_builder)
  common.py       reading .common.json, writing <cat>_gpa.csv and <cat>_courses.csv
  definitions.py  category-definitions in config.yaml
  definer.py      category-definer: modes, steps, retries and log
category_definition_builder/  drafts a definition via ChatGPT/Gemini in a browser
                  (python -m category_definition_builder STEM)
  browser_legs.py the two sites, browser choice, headless or not (from llm_compare)
  pipeline.py     one browser try per process, with a hard deadline
  client.py       prompt and DEFINITION: ... END parsing
  chain.py        try steps in turn, recording each attempt
llm_category/     the llm builder (python -m llm_category)
  builder.py      load_config / run / estimate
  llm.py          the Ollama prompts
  store.py        answer cache
jev_category/     the jev builder (python -m jev_category)
  builder.py      load_config / run / estimate
  client.py       TypeSafe API client
  store.py        score cache
out_builder/      publish <cat>_gpa.csv to out-folder (python -m out_builder)
letter_builder/   recommendation letters: pdf2txt, then rank_letters with Jev
browser_view/     Flask UI: transcript and letter pages (python -m browser_view)
install/          per-user installer scripts (used by /install-gpa-tools)
tests/
```

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

The tests need neither Ollama, pdftotext, a browser nor TypeSafe: all are
replaced by fakes. They cover transcript conversion and GPA, the builder
registry and CSV output, both builders and their caches, definitions, the
definer's modes and attempt log, reply parsing, which model does which job,
publishing, every browser_view route, and the installer's config editing and
model choice.

## Changes

### 2026-11-08: input and work folders per component

Raw input and the files the tools make from it are now kept apart. Before,
`pdf-folder`, `letters-folder` and `personal-folder` each mixed the two: the
letters folder held the PDFs next to their `.txt` and `letter_ranks.csv`, the
personal folder held each `.profile` next to its `.json`, and uploaded
transcript PDFs were copied into `work-folder`.

| | before | now |
|---|---|---|
| settings | `pdf-folder`, `letters-folder`, `personal-folder`, `work-folder` | `components: [transcripts, letters, personal]`, `input-folder`, `work-folder` |
| transcript PDFs (and uploads) | `pdf-folder` (uploads in `work-folder`) | `<input-folder>/transcripts/` |
| `<name>.txt`, `<name>.common.json` | `<work-folder>/` | `<work-folder>/transcripts/` |
| letter PDFs | `letters-folder` | `<input-folder>/letters/` |
| letter `.txt`, `letter_ranks.csv` | `letters-folder` | `<work-folder>/letters/` |
| `<name>.profile` | `personal-folder` | `<input-folder>/personal/` |
| profile `<name>.json` | `personal-folder` | `<work-folder>/personal/` |
| builder CSVs and caches, browser profiles | `<work-folder>/<builder>/`, `<work-folder>/browser-profiles/` | unchanged |

- An input folder is never written by a run (uploads are saved there, as
  input), and a work folder holds only files that can be made again from it.
- `input-folder` and `work-folder` have no default: a missing one is reported
  (KeyError on the command line, a message on the page) instead of falling
  back to `~/Templates`.
- One module, `folders.py`, works out every path. `letter_builder` CLIs take
  `--folder` (letter PDFs) and `--work` (their `.txt` and ranks);
  `pdf2txt.convert_one(pdf, work)` and `rank_letters.rank_pdfs(client, pdfs, work)`
  now name the work folder.
- `install/configure.py --data D` sets `input-folder: D` and `work-folder: D/work`,
  and creates the three component folders under each; `--pdf-folder` became
  `--input-folder`. `install/check.py` reports the three old keys as outdated.

Moving an existing setup over:

1. In `config.yaml`, replace the three old keys with `components:` and
   `input-folder:` (see Settings), and keep `work-folder:`.
2. Put the raw input in `<input-folder>/transcripts/` (`*.pdf`),
   `<input-folder>/letters/` (`*.pdf`) and `<input-folder>/personal/` (`*.profile`).
3. Delete the old generated files: `*.txt` and `*.common.json` in `work-folder`, and
   anything other than the input above in the input folders.
4. Rebuild them: Upload → Transcripts, Upload → Profiles (after the transcripts,
   which fill in `institution` and `program`) and Recommendations → Upload, each
   with no files chosen. The builders' caches stay where they were and are reused.
   Letter ranks come from Jev again and can differ from the old ones on borderline
   letters.
