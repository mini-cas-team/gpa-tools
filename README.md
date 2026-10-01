# gpa-ana

Rank students by average **category GPA** (STEM by default) read out of student
transcript PDFs.

```bash
.venv312/bin/python -m gpa_ana            # uses ./config.yaml
```

```yaml
# config.yaml
pdf-folder: /home/personal/Templates/transcripts
category: [STEM] # every listed category is ranked in the same run
```

`category` is a list. Several categories are ranked off **one** parse of each
PDF — membership is a set, so the rulesets accumulate onto the same courses
rather than competing — and each writes its own `rank_<id>_gpa.csv`/`.json` and
`extracted/<student>.<id>.courses.json`.

No model is called at run time. Reading a new transcript format is a
design-time activity that produces a profile YAML; the shipped app is ordinary
deterministic Python.

## Getting started

### 1. Clone and install

```bash
git clone https://github.com/johnprofessional132/gpa-tools.git
cd gpa-tools

python3.12 -m venv .venv312
.venv312/bin/pip install -r requirements.txt
```

Two command-line tools are called directly and are not Python packages:
`pdftotext` for pages with a text layer, and `pdftoppm` + `tesseract` for
scanned pages. On Debian or Ubuntu:

```bash
sudo apt install poppler-utils tesseract-ocr
```

`pdftotext` is required for every run; the other two only for scanned
transcripts. A missing tool is reported by name rather than failing obscurely.

Then point `config.yaml` at your transcripts:

```yaml
pdf-folder: /path/to/transcripts
out-folder: /path/to/output
category: [STEM]
```

### 2. Teach it new transcripts and new categories

Two skills ship with the repo, in `.claude/skills/`. Run them from a Claude
Code session started in the project root — they appear as slash commands once
the session picks them up, and a session started before they existed needs a
restart to see them.

| command | use it when |
|---|---|
| `/add-school-profile` | new PDFs arrive that no profile claims — a run reports `no layout profile matched confidently (score 0)`, or the table shows `(unrecognised institution)` |
| `/add-category <NAME>` | you want to rank on a new taxonomy, e.g. `/add-category MEDICAL` |

`/add-school-profile` decides whether the school joins an existing shared
profile or needs one of its own, then writes the profile and its department
map. Its reconnaissance step is a plain script you can also run yourself:

```bash
.venv312/bin/python .claude/skills/add-school-profile/scripts/inspect_unclaimed.py
```

`/add-category MEDICAL` writes `gpa_ana/categories/rulesets/medical.yaml` **and**
appends `MEDICAL` to the `category` list in `config.yaml`, so the next run ranks
it alongside whatever is already there.

Neither skill is required to *run* the tool — they are for teaching it formats
and taxonomies it does not yet know. Both edit YAML; only a genuinely novel
course-code shape needs Python.

### 3. Rank the students

```bash
.venv312/bin/python -m gpa_ana                          # every category in config.yaml
.venv312/bin/python -m gpa_ana --category MEDICAL       # just one, ignoring the list
.venv312/bin/python -m gpa_ana --category STEM,MEDICAL  # an explicit set
.venv312/bin/python -m gpa_ana -v                       # per-file detail
```

Each category prints its own ranked table and writes
`rank_<id>_gpa.csv`, `rank_<id>_gpa.json` and
`extracted/<student>.<id>.courses.json` into `out-folder`. Validation gates and
warnings are reported once for the run, since they describe the parse rather
than the taxonomy.

### 4. Or do it in a browser

```bash
.venv312/bin/pip install Flask        # only the web UI needs it
.venv312/bin/python -m gpa_ana.web    # http://127.0.0.1:5000
```

The form takes a **PDF input** -- either uploaded files or a folder path on this
machine -- an **output path**, and which categories to rank. `config.yaml`
prefills it when there is one, so the usual run is one click. Results come back
as ranked tables per category, with the held-back list, the gate tally, parse
warnings and CSV/JSON download links.

| flag | |
|---|---|
| `-c PATH` | config to prefill the form from (default `config.yaml`) |
| `--host` / `--port` | bind address, default `127.0.0.1:5000` |
| `--debug` | Flask reloader and tracebacks |

Both front ends call the same `service.analyse`, so the browser cannot drift
from the CLI -- `tests/test_web.py` pins that by diffing the CSV the two produce
from identical inputs.

**This is a localhost tool for one operator.** The folder fields are real server
paths, which is the point -- it reads a transcript folder already on the machine
and writes results next to it. Flask's development server is what `--host`
binds, and neither it nor the path fields belong on a network without
authentication and a path allowlist in front of them.

## Design: two independent axes

Parsing ends at a canonical `Course` record; categorisation begins from it.
Neither side imports the other.

```
PDF ──► extract ──► layout profile + handler ──► Course ──► category ruleset ──► rank
        text/OCR    "what shape is this page?"            "is this course STEM?"
```

| Axis | Lives in | Adding to it |
|---|---|---|
| **Layout** | `layout/profiles/*.yaml` over `layout/handlers/*.py` | A new school is usually **a YAML file and zero Python**. A new handler is only needed when the page *shape* is novel. A family of schools printing the *same* transcript shares one profile and costs **one fingerprint line** — see `md_common.yaml`, which serves 38. |
| **Category** | `categories/rulesets/*.yaml` | A new taxonomy (medical, humanities) is one YAML file. Layout code is untouched. |

`Course.categories` is a **set**, so a course can be STEM *and* medical at once.

Capability grows by accretion: each new format or taxonomy is an added file,
never a rewrite.

## What the corpus forced

Built against 14 transcripts from 11 US institutions. Three things that a
hard-coded parser would have got wrong:

**Column count is discovered, never assumed.** The header row is split into
bands and every data cell is assigned to the band it overlaps. Five columns,
seven columns — the handler does not care. Labels are *not* reliable: Columbia
prints `Grade  Points  Points`, where the first "Points" means credits, so
semantic roles are resolved by position relative to `Grade`.

**Grade scales differ, so raw GPA is not comparable.** Five distinct scales
appear in 14 files, including MIT's `A = 5.00`. Every transcript prints its own
`GRADING KEY`; it is parsed per file and normalised by the value the school
gives a plain A. Without this, a 5.00-scale student tops the table for
identical work.

**Department tokens collide across schools.** Caltech `PS` is Political
Science, Princeton `ENG` is English. Department maps are therefore per
institution, and a department in neither the include nor the exclude list is
reported as unmapped rather than silently treated as non-STEM.

## Validation gates

Every transcript states its own answer — printed term totals, cumulative GPA,
per-row quality points. Recomputing those from the parsed rows is a free
ground-truth oracle on every file, including files nobody has checked by hand.

It catches errors that raise no exception: a credit misread as `1` instead of
`4` parses fine, but the recomputed total no longer matches and the file is
flagged. A pipeline that reports "I could not read 6 of these 300" is
trustworthy; one that silently mis-parses 6 and reports success is worse than
useless, because students get ranked on it.

Gates run at four levels: per row, per term (credits and GPA), and per
transcript (total credits and cumulative GPA). A transcript that fails any gate
is **held back from the ranking**, never silently included.

Where a school prints no aggregate — Princeton publishes no GPA by policy — the
gate does not exist and is reported as `none`, not as a pass.

## Output

`out/rank_stem_gpa.json`, `out/rank_stem_gpa.csv`, and per-student course
detail in `out/extracted/*.courses.json` with full provenance (file, page,
line) for every row.

Alongside the category GPA each row carries `all_courses_GPA` (the same capped
average over every GPA-bearing course on the transcript), `total_courses`, and
`<id>_all_pct` — the category's share of those courses. All three count only
courses that carry a usable grade and count toward GPA, so the share is over
the same population the two averages are computed from.

Every student is ranked by one rule: **capped** GPA. Each course is rescaled so
a plain A is worth 4.00 at every school, then capped at 4.00.

The two steps answer the two ways a grading scale distorts a cross-school
comparison. Rescaling handles a different printed maximum — MIT prints A = 5.00
and would otherwise sweep the table for identical work. Capping handles schools
that award A+ *above* 4.00, a ceiling students elsewhere cannot reach however
well they do.

What capping gives up, deliberately: it equalises the ceiling, not the
distribution. Grade inflation, curve severity and department difficulty are
untouched, and straight-A+ is indistinguishable from straight-A once both land
on 4.000. The figure is comparable; the achievement behind it is not
necessarily.

`raw` — the average on the school's own printed scale — is reported alongside,
because it is the only figure that can be checked against the transcript as
printed. It is never used for ordering.

## Scanned pages

Format detection is per page, not per file: a page carrying a usable text layer
is read with `pdftotext -layout`, and a page without one is rendered, OCR'd and
rebuilt into the same column-aligned text. Layout handlers never learn which
path a page took, so a folder can mix born-digital and scanned transcripts
freely.

Three things the scanned path has to get right, each verified by a test:

- **Regions partition the page.** Gutters are found by projection profile, but
  a fragment narrower than a quarter of the page is a table column, not an
  independent block, so it is merged into its neighbour. Dropping it would drop
  the Grade and Credits columns outright.
- **Read per region, rebuild globally.** Cropping keeps `--psm 6` applying
  within a uniform block, which is what stops tesseract welding neighbouring
  blocks into one line. Every word is then placed back at its true page
  position, so a single table reassembles even when the split guessed wrong.
- **Column positions are approximate.** Word coordinates are converted to
  character columns via a median character width, so offsets drift a character
  or two along a line. Cells bind to a column's *territory* with a small
  tolerance, not to the label's own narrow extent.

Grade cells get a documented repair for glyph confusions (`Bt` → `B+`), applied
only when the cell is not already a valid grade. Every repair is still subject
to the `row_points` gate, so a wrong repair fails loudly rather than quietly
changing someone's GPA, and each one is reported as a warning.

The Harvard sample parses identically from both paths — same 32 courses, same
2.957 STEM GPA, all gates passing — which is the check that the OCR path agrees
with the text path rather than merely producing something.

## Performance

A text-layer transcript parses in ~20ms. A scanned one costs whole seconds, so
OCR is the only thing worth tuning.

The dominant factor is not resolution or engine choice — it is **tesseract's
internal OpenMP threading**, which is actively harmful on transcript-sized
pages. The same page, same flags:

| | default threads | `OMP_THREAD_LIMIT=1` |
|---|---|---|
| 300 dpi | 2.42s | 2.05s |
| 200 dpi | **20.69s** | 1.85s |

The pathological case is not rare and not deterministic — timings for identical
work ranged from 2.9s to 24.4s run to run. Pinning tesseract to one thread
makes each page consistently fast *and* leaves the cores free for parallelism
at a level that actually scales. Lowering the resolution buys almost nothing
once threading is fixed, so it stays at 300 dpi where accuracy is best.

Three structural fixes came with it: one `pdftoppm` pass per file instead of
one per scanned page, no re-encoding a "crop" that covers the whole page, and
OCR concurrency bounded by a semaphore in the extractor rather than by
choosing between file-level and page-level pools. That last point matters when
one slow scan sits among many fast files — it still gets the whole machine.

```yaml
jobs: 0        # 0 = one worker per CPU;  CLI: -j N
ocr-dpi: 300   # lower is not faster once threading is pinned
```

Measured on this corpus: the scanned file went from 26.6s to ~7s, and the test
suite from ~50s to ~8s. Take the parallel numbers as directional rather than
precise — the machine they were taken on was already running at a load average
of 4.4 on 4 cores, which is also why raw timings scattered so widely.

## Known limits

- **Only one handler exists.** `term_block_table` covers the modal US registrar
  layout. Side-by-side course tables and dual-semester grids need their own
  handlers.
- **Few-course majors rank on thin evidence.** A linguistics major with 3 STEM
  courses is ranked against an engineer with 39. Set `min-category-courses` in
  `config.yaml` to hold those back. The threshold counts *courses*, not credits,
  because credit units are not comparable across schools — one course is 1 unit
  at Penn, 100 at Chicago and 3–4 credits nearly everywhere else, so a
  credit-denominated gate means a different thing at every institution.
- **Category boundaries are judgement calls.** Psychology, economics,
  linguistics and cognitive science are excluded; engineering management and
  science-survey courses are included. All are one line each in
  `categories/rulesets/stem.yaml` — see the `notes` block there.

## Layout

```
config.yaml
gpa_ana/
  model.py        Course / Transcript / GradeScale -- the contract between axes
  extract.py      PDF -> page text (native layer, or render+region-split+OCR)
  layout/         profiles (YAML, per institution) + handlers (algorithms)
  categories/     rulesets (YAML, per taxonomy)
  grading.py      grade scales read from the document, normalised
  validate.py     the gates
  rank.py         aggregation and ranking
  cli.py          python -m gpa_ana
tests/
```

## Tests

```bash
.venv312/bin/python -m pytest tests/ -q
```

Unit tests cover column discovery, code grammars, scale normalisation and the
colliding department tokens. The integration test re-parses the whole corpus
and asserts every gate still passes — the regression net for adding a profile.
