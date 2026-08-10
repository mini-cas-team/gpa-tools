---
name: add-school-profile
description: Teach gpa-ana to read a transcript format it does not recognise. Use when new PDFs land in the transcripts folder, when a run reports "no layout profile matched confidently (score 0)", when the ranking shows "(unrecognised institution)", or when asked to add/support a new school or college.
---

# Adding a school to gpa-ana

A run that reports `no layout profile matched confidently (score 0)` parsed that
file with the generic fallback. It still produces numbers, but they are not
trustworthy: the department map is a broad guess, so economics courses with
"Analysis" in the title get counted as STEM.

Work through the steps in order. **Step 4 is not optional** — skipping it makes
results worse than leaving the school unrecognised.

## 1. See what the parser sees

```bash
.venv312/bin/python .claude/skills/add-school-profile/scripts/inspect_unclaimed.py
```

No arguments scans the whole configured folder and reports only unclaimed files.
Pass explicit paths to inspect any file. For each it prints the four things the
next decision turns on — header bands, grade scale, code grammar, department
vocabulary — and flags when an existing profile family already matches all four.

If it reports **no course rows parsed**, stop: the page *shape* is novel and
needs a new handler in `gpa_ana/layout/handlers/`, which is a much larger job
than a profile. Everything below assumes the rows parse.

## 2. Decide: join a family, or write a new profile?

Share one profile when **all four** match an existing profile's files:

| | how to check |
|---|---|
| header bands | same label tuple, e.g. `('Course','Title','Grade','Credits','Points')` |
| grade scale | same anchor and source |
| code grammar | the same `code_grammar` yields sensible tokens |
| department semantics | same tokens **and the tokens mean the same thing** |

The first three are observations; the fourth is a judgement call and the only
one that needs thought. Tokens collide across schools — Caltech `PS` is
Political Science, Princeton `ENG` is English, Stony Brook `AMS` is Applied
Mathematics while UMBC's `AMST` is American Studies. Identical spelling is not
identical meaning.

**Joining a family is the cheap path.** Add the school's printed name to that
profile's `fingerprint.text_any` and skip to step 5 — no new file, no new
ruleset block. `md_common.yaml` serves 38 schools this way.

**Write a new profile** when any of the four diverges.

## 3. Write the profile

`gpa_ana/layout/profiles/<id>.yaml`:

```yaml
id: some_school                     # also the ruleset key -- keep it stable
institution: Some School            # display name
handler: term_block_table
fingerprint:
  text_any: ["SOME SCHOOL", "Some School ID:"]   # +10 each
code_grammar: space_suffix          # or mit_dotted | jhu_dotted | alpha_prefix
validation:                         # omit unless a gate does not apply
  cumulative_gpa: false             # e.g. Princeton prints no GPA by policy
notes: >
  Why this school is not like the others.
```

Set `institution_from: first_line` instead of relying on `institution` when one
profile serves many schools; the name is then read off the page and
`institution` becomes a fallback label.

If no `code_grammar` produces a sensible department token, add a branch to
`Profile.dept_of` in `gpa_ana/layout/__init__.py`. That is the only Python a new
school normally needs. Existing branches:

- `space_suffix` — everything before the final token: `COMS W1004` → `COMS`,
  `CSCI-UA 101` → `CSCI-UA`, `CIV ENG 100` → `CIV ENG`
- `mit_dotted` — `6.036` → `6`
- `jhu_dotted` — `AS.270.103` → `270`
- `alpha_prefix` — `SM221` → `SM` (no separator at all)

### The prefix trap — check this every time

Institution names nest, and `text_any` is a substring match. `"UNIVERSITY OF
MARYLAND"` silently claimed five different schools for College Park, and
`"UNIVERSITY OF MARYLAND, BALTIMORE"` is in turn a prefix of `"... BALTIMORE
COUNTY"`. Always name the school in full.

Verify both directions — the new profile must not claim other files, and other
profiles must not claim its file:

```bash
.venv312/bin/python - <<'EOF'
from pathlib import Path
from gpa_ana.extract import extract_pages, clean_lines
from gpa_ana.layout import load_profiles
for p in sorted(Path("/home/personal/Templates/transcripts").glob("*.pdf")):
    text = "\n".join(l for pg in extract_pages(p) for l in clean_lines(pg.text))
    scores = {pr.id: pr.score(text) for pr in load_profiles() if pr.score(text)}
    if len(scores) > 1:
        print(f"{p.name}: contested by {scores}")
EOF
```

When two profiles genuinely tie, the more specific one must win. Dispatch takes
the highest score and ties are decided by filename order, which is not a
decision anyone made — so break the tie deliberately by adding an anchored
`regex_any` (worth +8) to the specific profile, and pin it with a test. See
`umbc.yaml`.

## 4. Extend the ruleset — mandatory

`gpa_ana/categories/rulesets/stem.yaml`, under `institutions:`:

```yaml
  some_school:
    include: [MATH, PHYS, CHEM, BIOL, CS]
    exclude: [ART, ECON, ENGL, HIST, MUSIC, PHIL, PSYC]
```

**Why this cannot be skipped.** `ruleset.py:59-85` branches on whether the
institution is known. Unknown → a broad generic list plus title keywords.
Known → the school's own lists, and any token in *neither* list falls to
`bool(title_hit)`, so it is excluded unless its title happens to trip a keyword.
Adding a profile without a ruleset block therefore *removes* the fallback and
drops real STEM courses. Every token the school uses must appear in one list or
the other.

Follow the judgement calls already recorded in that file's `notes` block
(psychology/economics excluded, engineering management included, and so on) so
one school is not scored on a different standard than the rest.

## 5. Verify

```bash
.venv312/bin/python -m pytest tests/ -q
```

Then run the app against a scratch config so real results are not overwritten
while iterating — copy `config.yaml`, repoint `out-folder`, and pass `-c`.

All four must hold for the new file:

1. no `no layout profile matched confidently` warning
2. no `(unrecognised institution)` in the table
3. no `departments not in the STEM ruleset` warning
4. its validation gate passes

A gate failure after the profile matches usually means a genuine parse loss, not
a bad profile — read `failed_checks` in
`out/extracted/<student>.courses.json`, which names the term and the printed vs
parsed figures.

Add a regression test in `tests/test_pipeline.py` for anything resolved by score
rather than by an unambiguous fingerprint; those break silently.
