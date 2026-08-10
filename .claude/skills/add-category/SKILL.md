---
name: add-category
description: Create a new course category ruleset for gpa-ana (MEDICAL, HUMANITIES, BUSINESS, ...) so students can be ranked on it, or edit an existing one such as STEM. Use when asked to add a category/taxonomy, change what counts as a subject area, resolve "departments not in the <X> ruleset" warnings, or when load_ruleset raises "no ruleset for category".
---

# Adding a category to gpa-ana

A category is **one YAML file and zero Python**:
`gpa_ana/categories/rulesets/<name>.yaml`. It decides which courses count
toward the ranked GPA. `stem.yaml` is the worked example throughout.

Membership is a *set*: a course can be STEM and MEDICAL at once, so a new
category never disturbs an existing one.

## How a ruleset decides — read this first

`Ruleset.classify` (`gpa_ana/categories/ruleset.py:59-85`) resolves each course
in a fixed order:

1. dept in this school's `include` → **in** (flagged `disputed` if the title
   disagrees)
2. dept in this school's `exclude` → **out** (same flag)
3. **school has no block at all** → dept in `generic_include` → in; else a
   title keyword decides; else out — and either way it is reported *unmapped*
4. **school has a block but this dept is in neither list** → `bool(title_hit)`,
   reported unmapped

The asymmetry between 3 and 4 is the thing to understand. `generic_include` is
consulted **only for schools with no block of their own**. The moment you add
an institution block, that school stops using the generic list entirely, so
every token it uses must appear in its own `include` or `exclude`. Add blocks
late and deliberately, not by reflex.

## 1. Create the file

The filename must be the category name lowercased — `load_ruleset` resolves
`category: STEM` to `rulesets/stem.yaml`. A minimal file is already valid:

```yaml
id: STEM
description: Science, Technology, Engineering and Mathematics coursework.
```

`id` is the tag written onto courses and the name used for outputs: it drives
the console header, `rank_stem_gpa.csv`/`.json`, and the `STEM_GPA` column.
Keep it stable once results are circulating.

## 2. Let the corpus enumerate the vocabulary

Do not guess department tokens. Run the app and let it tell you:

```bash
.venv312/bin/python -m gpa_ana --category STEM
```

Every token it cannot place is reported as
`departments not in the STEM ruleset: ECON x15, HUMA x2, ... `. **That list is
the worklist** — with counts, so the highest-volume decisions surface first. A
near-empty ruleset tags nothing and enumerates everything, which is the
intended starting state; on this corpus that is 128 distinct tokens across 19
institutions.

Iterate: classify a batch, re-run, watch the list shrink. Done when it is empty.

## 3. Expect one institution block per school

**There is no `generic_exclude`.** Read `classify` again: on the no-block path,
a token in `generic_include` is in, and *everything else* — whether you consider
it excluded or simply have not thought about it — returns with `unmapped=True`
and is reported. Silence therefore cannot be bought with a generic list alone;
it takes an institution block naming every token that school uses, in `include`
or in `exclude`.

So budget for a block per school. STEM has 21, MEDICAL has 19. That sounds
heavier than it is: step 2 hands you each school's vocabulary, and most blocks
are two lines.

`generic_include` is still worth writing carefully — it is not a shortcut but
the **fallback for schools you have not mapped yet**, which is the normal state
right after new transcripts arrive. It decides whether an unmapped school gets
roughly-right numbers or none at all:

```yaml
generic_include:
  [MATH, MATHS, MTH, STAT, STATS, PHYS, PHYSICS, CHEM, CHEMISTRY, BIO, BIOL,
   BIOLOGY, CS, COMPSCI, CSE, EECS, EE, ECE, ENGR, ENGIN, ENGINEERING, ...]
```

```yaml
generic_include:
  [MATH, MATHS, MTH, STAT, STATS, PHYS, PHYSICS, CHEM, CHEMISTRY, BIO, BIOL,
   BIOLOGY, CS, COMPSCI, CSE, EECS, EE, ECE, ENGR, ENGIN, ENGINEERING, ...]
```

Tokens are compared case- and whitespace-normalised (`_norm`), so `civ eng` and
`CIV  ENG` both match `CIV ENG`. Numeric departments must be quoted (`"020"`,
`"5"`): unquoted, YAML makes them integers and loading fails loudly with
`TypeError: expected string or bytes-like object, got 'int'`.

The blocks are also what handles tokens meaning different things at different
schools — the reason the mechanism is keyed by institution at all:

```yaml
institutions:
  caltech:
    include: [ACM, AY, BI, CH, CS, EE, MA, PH]
    exclude: [EC, EN, H, L, MU, PS]   # PS = Political Science at Caltech
  princeton:
    include: [MAT, PHY, CHM, MOL, NEU, COS, GEO, ORF, CBE, ELE, MAE]
    exclude: [ANT, ART, ECO, ENG, FRE, MUS, PHI, POL, PSY, WRI]   # ENG = English
```

Real collisions in this corpus: Caltech `PS` is Political Science, Princeton
`ENG` is English, Stony Brook `AMS` is Applied Mathematics while UMBC `AMST` is
American Studies, MIT departments are bare numbers.

Remember rule 4: once `caltech:` exists, *every* Caltech token needs a home in
one of its two lists.

## 4. Title keywords are advisory, not a second map

```yaml
title_audit:
  include_keywords: [calculus, algebra, probability, statistics, physics, ...]
  exclude_keywords: [political science, history of, literature, philosophy, ...]
```

Three things to know:

- **Exclude wins.** `_title_says_in` tests exclude keywords first and returns
  immediately.
- **Match is word-boundary-prefixed**, not whole-word: `computer` also matches
  `computerized`. Prefer specific phrases (`history of`) over bare words.
- Against a *mapped* department the keywords never override the map — they only
  raise `disputed`, which surfaces a suspect mapping instead of hiding it.

Keywords are a safety net for unmapped schools. They are also a trap, in both
directions:

- `analysis` counted *The Elements of Economic Analysis I–IV* as STEM until
  Chicago got an explicit `exclude: [ECON]`.
- `mechanics`, added to MEDICAL as a physics word, matched *Elementary Fluid
  Mechanics* and *Mechanics of Materials*. Neither title contains
  "engineering", so the exclude list never fired first. Eight false disputes,
  and the keyword bought nothing because every physics department was already
  mapped.

The rule that falls out: once a department is mapped everywhere it appears, a
keyword aimed at that same subject can only produce noise. Add keywords for
subjects the maps do *not* already cover.

Once the maps are complete, `disputed` is the only signal left, so read it —
it is cheap and it is where mistakes surface. Every entry deserves one of two
outcomes: fix the map, or record in `notes` why the map wins. MEDICAL keeps
exactly one standing: Cornell `CHEM 2090 "Engineering General Chemistry"` is
mapped in while "engineering" excludes it by title, and the map is right.

`apply_categories` returns the disputes but the CLI does not print them, so ask
for them directly:

```bash
.venv312/bin/python - <<'EOF'
from pathlib import Path
from gpa_ana.categories import load_ruleset
from gpa_ana.layout import load_profiles
from gpa_ana.pipeline import parse_transcript, apply_categories
ruleset, profiles = load_ruleset("MEDICAL"), load_profiles()
for pdf in sorted(Path("/home/personal/Templates/transcripts").glob("*.pdf")):
    transcript = parse_transcript(pdf, profiles)
    audit = apply_categories(transcript, ruleset)
    for entry in audit["disputed"]:
        print(f"{transcript.file}: {entry}")
EOF
```

## 5. Write the judgement calls down

Anything arguable goes in `notes` so it can be argued with rather than
rediscovered. From `stem.yaml`:

> - Psychology, economics, linguistics and cognitive science are treated as NOT
>   STEM. Each is defended as STEM somewhere; move the token from "exclude" to
>   "include" for a school to flip it.
> - Environmental *studies* (policy framing) is excluded; environmental
>   *science* and earth science departments are included.
> - General-education science surveys (Columbia SCNC, Chicago PHSC) are included
>   on content; Harvard GENED is excluded because the department spans all
>   subjects.

A reviewer should be able to disagree with a specific line rather than with the
whole file.

## 6. Register it in config.yaml

`category` is a **list**, and every name in it is ranked in the same run off one
parse of each PDF. Append the new category rather than replacing what is there:

```yaml
category: [STEM, MEDICAL] # one or more; every listed category is ranked in the same run
```

A bare string is still accepted and read as a list of one, and duplicates are
dropped, so appending is always safe. This registration step is the point of
invoking the skill with a name — `/add-category MEDICAL` both creates
`rulesets/medical.yaml` and adds `MEDICAL` to this list.

`--category` overrides the list for one run and takes several names
comma-separated:

```bash
.venv312/bin/python -m gpa_ana --category MEDICAL
.venv312/bin/python -m gpa_ana --category STEM,MEDICAL
```

## 7. Run and verify

An unknown name fails fast and lists what exists.

```bash
.venv312/bin/python -m pytest tests/ -q
.venv312/bin/python -m gpa_ana
```

Check all four:

1. no `departments not in the <ID> ruleset` warnings remain
2. no `disputed` entries you have not deliberately accepted
3. the ranked count is plausible — a category almost nobody matches usually
   means tokens were classified into `exclude` that should have been `include`
4. outputs land as `rank_<id>_gpa.csv` / `.json` with the `<ID>_GPA` column, and
   per-student detail as `extracted/<student>.<id>.courses.json`

Ranking mechanics — capped GPA, the `min-category-courses` gate — are shared by
every category and need no per-category setup.
