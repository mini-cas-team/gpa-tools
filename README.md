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
.venv312/bin/python -m browser_view      # http://127.0.0.1:5001
```

## Getting started

```bash
git clone https://github.com/johnprofessional132/gpa-tools.git
cd gpa-tools
claude                     # then type:  /install-gpa-tools
```

The `/install-gpa-tools` skill does the rest in your own user account, with no
admin rights, on Linux, macOS or Windows:
- the venv and packages (Playwright among them; it drives the installed Chrome or Edge)
- pdftotext (into `.tools/`, if it's missing)
- Ollama and two qwen2.5 models sized to the machine: a fast one for building (fits in the GPU, if there is one) and the largest that fits for drafting definitions
- `config.yaml`: folders, the two models and the Ollama URL, the definer's mode and,
  optionally, the jev builder, set by `install/configure.py`

It checks what is already installed first and asks before downloading. See
[install/README.md](install/README.md) for the scripts it uses, which also
work by hand. `config.yaml` is tracked in git.

Or by hand, on Linux with admin rights:

```bash
python3.12 -m venv .venv312
.venv312/bin/pip install -r requirements.txt
sudo apt install poppler-utils                  # pdftotext
curl -fsSL https://ollama.com/install.sh | sh   # Ollama
ollama pull qwen2.5:3b                          # build model
ollama pull qwen2.5:7b                          # definition model
.venv312/bin/python install/configure.py --data ~/gpa-tools-data \
    --model qwen2.5:3b --definer-model qwen2.5:7b
```

## Usage

### Settings

```yaml
# config.yaml
pdf-folder: /home/personal/Templates/transcripts   # PDFs to convert
work-folder: /home/personal/Templates/work         # .txt, .common.json, uploaded PDFs; <builder>/ folders
out-folder: /home/personal/Templates/out           # published <cat>_gpa.csv, for other applications
log-folder: /home/personal/Templates/log           # upload, llm_category, jev_category, category_definer, browser_view .log
letters-folder: /home/personal/Templates/letters   # recommendation letter PDFs, their .txt and letter_ranks.csv
min-category-courses: 4      # View tab: hide students with fewer category courses
run_mode: dev                # dev: the student dialog's letters tab also shows Jev's rank and confidence

active-category-builder: jev # the default in the Upload and View tabs and the CLI
category-builder:            # one section per builder
  llm:
    category-build-model: qwen2.5:3b       # the llm builder's yes/no answers: the fast one
    category-definition-model: qwen2.5:7b  # category-definer's ollama step: as large as fits
    ollama-url: http://localhost:11434     # both models: any in `ollama list` there
  jev:
    model: jev-1.13.0        # a version pins it; `jev-latest` follows new releases
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

### 1. upload: PDF to common JSON

```bash
.venv312/bin/python -m upload            # every PDF in pdf-folder
```

For each PDF it writes two files into `work-folder`:

- `<name>.txt`, from `pdftotext -layout`.
- `<name>.common.json`, the common transcript format from
  `upload/common/txt2common.py`, with `gpa` and `gpa_4` added per course by
  `add_gpa.py`. This is what every category builder reads.

There is no OCR here: a scanned PDF without a text layer is reported and
skipped. Files with the same name are replaced.

### 2. Category builders: which courses are in the category

```bash
.venv312/bin/python -m category_builder --category STEM              # the active builder
.venv312/bin/python -m category_builder --category STEM --builder llm --publish
.venv312/bin/python -m llm_category --category STEM --limit 3       # llm only, 3 students
.venv312/bin/python -m jev_category --category STEM                 # jev only
```

A builder reads every `*.common.json` in `work-folder` and writes into its own
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
.venv312/bin/python -m category_definition_builder STEM --only chatgpt
.venv312/bin/python -m category_definition_builder STEM --mode prod --only gemini
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
.venv312/bin/python -m out_builder --category STEM [--builder llm]
```

Copies `<work-folder>/<builder>/<cat>_gpa.csv` to `<out-folder>/<cat>_gpa.csv`,
the public output for other applications. The name carries no builder:
out-folder holds whichever builder was published last for that category. The
Upload tab publishes after every run it starts; on the command line, a build
publishes only with `--publish` or this command.

### 4. browser_view: the web UI

```bash
.venv312/bin/python -m browser_view      # http://127.0.0.1:5001
```

| flag | |
|---|---|
| `-c PATH` | config file (default `config.yaml`) |
| `--host` / `--port` | bind address, default `127.0.0.1:5001` |
| `--debug` | Flask reloader and tracebacks |

A left bar lists the pages in two groups. **Transcripts**: *FTP Upload* (the
Upload panel below), *Category Build* (the Categories panel) and *View*.
**Recommendations**: *FTP Upload* and *View* for letters. `/` still shows both
transcript panels side by side.

**Upload tab.** Two panels, each with its own scroll bar.

- *Left, Upload:* choose PDFs to upload, or leave it empty to convert the whole
  `pdf-folder`. Each becomes `.txt` and `.common.json` in `work-folder`, as
  with `python -m upload`. Uploaded PDFs are saved there too, so View can open
  them.
- *Right, Categories:* an action bar of **Builder** and **Category** (dropdowns),
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
re-reads the folder, e.g. after a command-line run. Click a student to open
their details in two tabs: *Transcript*, their courses from `<cat>_courses.csv`
next to the transcript PDF, and *Recommendation letters*, each letter with its
post_rank rank next to the selected letter's PDF.
**Recommend letter** is the student's group rank from their recommendation
letters (`letter_builder/students.py`, `student_info`): letters
`<student>_letter<N>.pdf` in `letters-folder` match transcript `<student>.pdf`,
and their post_rank ranks combine into very excellent, excellent, very good,
good or normal; `–` when the student has no ranked letters.

**Recommendations, FTP Upload.** Choose letter PDFs, or leave it empty to take
every PDF in `letters-folder`. A letter whose `<name>.txt` is already there was
uploaded before and is skipped (not saved, not ranked again). New ones are
saved there; then, in the background with a progress bar, each becomes `<name>.txt` and Jev ranks it
*excellent*, *good* or *normal* (one `choice` question per letter, using the
`category-builder: jev:` model, URL and key). The ranks are merged into
`<letters-folder>/letter_ranks.csv` (`filename,rank,confidence`), replacing
rows for the same file. One run at a time.

**Recommendations, View.** One row per student with letters in
`letters-folder`: **Student**, the number of **Letters**, and the **Group rank**
from `student_info` (`letter_builder/students.py`), which combines the
student's letter ranks after `post_rank.py` (*good* below 0.7 becomes
*normal*; *excellent* below 0.4 becomes *normal*, and below 0.7 *good*;
`letter_ranks.csv` keeps Jev's own answers). Best group rank first; filter by
**Group rank**, sort by column. The student's name links to the same student
dialog as Transcripts → View, with the transcript's courses from the active
builder's first category.

The same steps run from the command line:

```bash
.venv312/bin/python -m letter_builder.pdf2txt        # every PDF in letters-folder -> .txt
.venv312/bin/python -m letter_builder.rank_letters   # every .txt -> letter_ranks.csv (rewritten)
.venv312/bin/python -m letter_builder.post_rank      # print the adjusted ranks
```

**This is a localhost tool for one operator.** It serves files from the
configured folders, and Flask's development server is what `--host` binds;
neither belongs on a network without authentication in front of it.

## Layout

```
config.yaml       folders, builders, definer, category definitions
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
.venv312/bin/python -m pytest tests/ -q
```

The tests need neither Ollama, pdftotext, a browser nor TypeSafe: all are
replaced by fakes. They cover transcript conversion and GPA, the builder
registry and CSV output, both builders and their caches, definitions, the
definer's modes and attempt log, reply parsing, which model does which job,
publishing, every browser_view route, and the installer's config editing and
model choice.
