"""Build labels.json from the first half of the transcripts, run on all, compare."""
import copy, json, re, sys
from pathlib import Path
import txt2common as tc

T = Path(sys.argv[1])
txts = sorted(T.glob("*.txt"))
train, test = txts[:32], txts[32:]

# --- build label list from training half only (structure: line N of STUDENT RECORD) ---
labels = {"student": {"name": set(), "student_id": set(), "major": set(), "degree": set(), "honors": set()},
          "student_position_fallback": tc.LABELS["student_position_fallback"],
          "credit_unit": set(), "period_type": set(), "season": set()}
grades, codes = set(), set()
for p in train:
    d = json.loads(p.with_suffix(".common.json").read_text())   # full-run output = ground truth
    s = d["student"]
    labels["student"]["name"].add("Name"); labels["student"]["degree"].add("Degree")
    labels["student"]["student_id"].add(s["id_label"]); labels["student"]["major"].add(s["major_label"])
    if s["honors"]: labels["student"]["honors"].add("Honors")
    labels["credit_unit"].add(d["credit_unit"])
    for y in d["years"]:
        for t in y["terms"]:
            labels["season"].add(t["season"]); labels["period_type"].add(t["period_type"].capitalize())
    grades |= {g for g, v in d["grading_key"]["grade_points"].items() if v is not None}
    codes |= {c for c, v in d["grading_key"]["codes"].items() if v is not None}
labels = {k: ({kk: sorted(vv) for kk, vv in v.items()} if isinstance(v, dict) else sorted(v) if isinstance(v, set) else v)
          for k, v in labels.items()}
(Path("half_labels.json")).write_text(json.dumps(labels, indent=2) + "\n")

full_labels, full_template = tc.LABELS, tc.TEMPLATE
half_template = copy.deepcopy(full_template)
half_template["grading_key"]["grade_points"] = {g: None for g in full_template["grading_key"]["grade_points"] if g in grades}
half_template["grading_key"]["codes"] = {c: None for c in full_template["grading_key"]["codes"] if c in codes}
tc.LABELS, tc.TEMPLATE = labels, half_template

print("TRAIN labels:", json.dumps(labels["student"]["student_id"]), "\n  major:", labels["student"]["major"],
      "\n  credit_unit:", labels["credit_unit"], "\n  period_type:", labels["period_type"], "season:", labels["season"])
print("  grades:", sorted(grades), "\n  codes:", sorted(codes))
print()

def strip(d):
    d = copy.deepcopy(d); d.pop("warnings")
    d["grading_key"] = {k: ({kk: vv for kk, vv in v.items() if vv is not None} if isinstance(v, dict) else v)
                        for k, v in d["grading_key"].items()}
    return d

stats = {"train": [0, 0, 0], "test": [0, 0, 0]}   # files, files-with-warnings, files-wrong
for group, files in (("train", train), ("test", test)):
    for p in files:
        got = tc.convert(p)
        truth = json.loads(p.with_suffix(".common.json").read_text())
        ok = strip(got) == strip(truth)
        stats[group][0] += 1; stats[group][1] += bool(got["warnings"]); stats[group][2] += not ok
        if got["warnings"] or not ok:
            print(f"[{group}] {p.name:24} {'CORRECT' if ok else 'WRONG'}")
            for w in got["warnings"]: print("      ", w)
            if not ok:
                for k in ("student", "credit_unit", "years", "summary_of_record", "grading_key", "institution"):
                    if strip(got)[k] != strip(truth)[k]: print("       differs in:", k)
print()
for g, (n, w, bad) in stats.items():
    print(f"{g}: {n} files, {w} with warnings, {bad} with wrong/missing values")
