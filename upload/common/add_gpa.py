"""Add a per-course GPA value to every *.common.json in a folder.

For each course the value comes from the first source that has it:
  1. school_rules/<institution>.json  "grade_points" -- manual override for a school
  2. the transcript's own grading key   (grading_key.grade_points)
  3. points / credits on the course row -- only when 1 and 2 have no entry

gpa_4 puts the same letter on one common 0-4 table (standard_scale.json) so
courses can be compared across schools; gpa keeps the school's own value.

Grades that are not on the GPA scale (P, W, CR, S, AU, ...) get gpa null and
counts_in_gpa false. Every value is checked against points / credits and every
term GPA is recomputed from the courses; disagreements go into "warnings".
"""
import json, re, sys
from pathlib import Path

RULES_DIR = Path(__file__).parent / "school_rules"
STANDARD = json.loads((Path(__file__).parent / "standard_scale.json").read_text(encoding="utf-8"))["grade_points"]
TOLERANCE = 0.006   # points are printed to 2 decimals
TERM_TOLERANCE = 0.011

def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

def load_rules(institution):
    path = RULES_DIR / f"{slug(institution)}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

def non_gpa_codes(doc, rules):
    """Grade codes that carry no grade points, e.g. 'P / F' -> {'P', 'F'} minus real letter grades."""
    codes = set(rules.get("non_gpa_grades", []))
    for key, meaning in doc["grading_key"]["codes"].items():
        if meaning is not None:
            codes |= {c.strip() for c in key.split("/")}
    return codes

def resolve(course, scale, rules_map, non_gpa):
    grade = course["grade"]
    if grade in rules_map:
        return rules_map[grade], "school_rule"
    if scale.get(grade) is not None:
        return scale[grade], "grading_key"
    if grade in non_gpa:
        return None, "non_gpa_grade"
    if course["credits"]:
        return round(course["points"] / course["credits"], 2), "derived_from_points"
    return None, "unresolved"

def add_gpa(doc):
    rules = load_rules(doc["institution"]["name"] or "")
    rules_map = rules.get("grade_points", {})
    scale = doc["grading_key"]["grade_points"]
    non_gpa = non_gpa_codes(doc, rules) - {g for g, v in scale.items() if v is not None} - set(rules_map)
    warn = doc.setdefault("warnings", [])
    for year in doc["years"]:
        for term in year["terms"]:
            total_pts = total_cr = 0
            for c in term["courses"]:
                gpa, source = resolve(c, scale, rules_map, non_gpa)
                c["gpa"], c["gpa_source"], c["counts_in_gpa"] = gpa, source, gpa is not None
                c["gpa_4"] = STANDARD.get(c["grade"]) if gpa is not None else None
                if gpa is not None and c["gpa_4"] is None:
                    warn.append(f"{term['term']} {c['code']}: grade {c['grade']!r} not in standard_scale.json; gpa_4 is null")
                if source == "derived_from_points":
                    warn.append(f"{term['term']} {c['code']}: grade {c['grade']!r} not in grading key; gpa {gpa} derived from points/credits")
                elif source == "unresolved":
                    warn.append(f"{term['term']} {c['code']}: grade {c['grade']!r} has no gpa value")
                elif gpa is not None and c["credits"] and abs(c["points"] / c["credits"] - gpa) > TOLERANCE:
                    warn.append(f"{term['term']} {c['code']}: gpa {gpa} for {c['grade']!r} but points/credits = {c['points'] / c['credits']:.2f}")
                if gpa is not None:
                    total_pts += gpa * c["credits"]; total_cr += c["credits"]
            printed = term["summary"]["term_gpa"]
            if total_cr and printed is not None and abs(total_pts / total_cr - printed) > TERM_TOLERANCE:
                warn.append(f"{term['term']}: term GPA from courses {total_pts / total_cr:.2f} != printed {printed}")
    doc["warnings"] = list(dict.fromkeys(warn))
    return doc

if __name__ == "__main__":
    for path in sorted(Path(sys.argv[1]).glob("*.common.json")):
        doc = add_gpa(json.loads(path.read_text(encoding="utf-8")))
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for w in doc["warnings"]:
            print(f"{path.name}: {w}")
    print("done")
