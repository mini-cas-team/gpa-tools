"""Convert pdftotext -layout transcripts into the common JSON format.

Every output has the same keys as template.json; values a transcript does not
print are null. Labels are matched against labels.json; an unknown label falls
back to its position and is reported in "warnings".
"""
import copy, json, re, sys
from pathlib import Path

HERE = Path(__file__).parent
LABELS = json.loads((HERE / "labels.json").read_text(encoding="utf-8"))
TEMPLATE = json.loads((HERE / "template.json").read_text(encoding="utf-8"))

FOOTER = re.compile(r"^.+ — page \d+$")
YEAR = re.compile(r"^YEAR (\d+): (.+)$")
TERM = re.compile(r"^(\w+) (\w+) (\d{4})$")
HEADER = re.compile(r"^Course\s+Title\s+Grade\s+(.+?)\s+Points$")
ROW = re.compile(r"^(?P<code>\S+(?: \S+)*?)\s{2,}(?P<title>.+?)\s{2,}(?P<grade>\S+)\s+(?P<credits>[\d.]+)\s+(?P<points>[\d.]+)$")
TSUM = re.compile(r"^(?P<label>.+?): (?P<cr>[\d.]+) Term GPA: (?P<gpa>[\d.]+)(?: Cumulative (?P=label): (?P<ccr>[\d.]+) Cumulative GPA: (?P<cgpa>[\d.]+))?$")
DEGREE = re.compile(r"^(?P<name>.+?)(?: \((?P<abbr>[^)]+)\))?(?:, conferred (?P<when>.+))?$")
GRADEPAIR = re.compile(r"(\S+) = ([\d.]+)")

def num(s):
    return float(s) if "." in s else int(s)

def student_field(label, position, warnings):
    for field, labels in LABELS["student"].items():
        if label in labels:
            return field
    fallback = LABELS["student_position_fallback"]
    if position < len(fallback):
        warnings.append(f"unknown student label {label!r}; used position {position + 1} -> {fallback[position]}")
        return fallback[position]
    warnings.append(f"unknown student label {label!r} at position {position + 1}; ignored")
    return None

def convert(path):
    doc = copy.deepcopy(TEMPLATE)
    doc["years"], doc["warnings"] = [], []
    warn = doc["warnings"]
    st, gk, sor = doc["student"], doc["grading_key"], doc["summary_of_record"]
    doc["source_file"] = path.with_suffix(".pdf").name

    letterhead, section, term, student_pos = [], "letterhead", None, 0
    for line in (l.strip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines()):
        if not line or FOOTER.match(line):
            continue
        if line in ("STUDENT RECORD", "SUMMARY OF RECORD", "GRADING KEY"):
            section = line; continue
        if m := YEAR.match(line):
            section = "years"
            doc["years"].append({"year": int(m[1]), "label": m[2], "terms": []}); continue

        if section == "letterhead":
            letterhead.append(line)
        elif section == "STUDENT RECORD":
            label, _, value = line.partition(": ")
            field = student_field(label, student_pos, warn)
            student_pos += 1
            if field == "student_id": st["student_id"], st["id_label"] = value, label
            elif field == "major": st["major"], st["major_label"] = value, label
            elif field == "degree":
                m = DEGREE.match(value)
                st["degree"], st["degree_abbreviation"], st["conferred"] = m["name"], m["abbr"], m["when"]
            elif field: st[field] = value
        elif section == "years":
            if line in letterhead:
                continue  # letterhead repeated on page 2
            if (m := TERM.match(line)) and m[2] in LABELS["period_type"]:
                if m[1] not in LABELS["season"]: warn.append(f"unknown season {m[1]!r} in {line!r}")
                term = {"term": line, "season": m[1], "period_type": m[2].lower(), "year": int(m[3]),
                        "courses": [], "summary": copy.deepcopy(TEMPLATE["years"][0]["terms"][0]["summary"])}
                doc["years"][-1]["terms"].append(term)
            elif m := HEADER.match(line):
                if m[1] not in LABELS["credit_unit"]: warn.append(f"unknown credit column label {m[1]!r}")
                doc["credit_unit"] = doc["credit_unit"] or m[1]
            elif m := ROW.match(line):
                term["courses"].append({"code": m["code"], "title": m["title"], "grade": m["grade"],
                                        "credits": num(m["credits"]), "points": num(m["points"])})
            elif m := TSUM.match(line):
                term["summary"].update(credits=num(m["cr"]), term_gpa=num(m["gpa"]))
                if m["ccr"]:
                    term["summary"].update(cumulative_credits=num(m["ccr"]), cumulative_gpa=num(m["cgpa"]))
            else:
                warn.append(f"unparsed line: {line!r}")
        elif section == "SUMMARY OF RECORD":
            if m := re.match(r"^Total .+ Earned: ([\d.]+)$", line):
                sor["total_credits_earned"] = num(m[1])
            elif m := re.match(r"^Cumulative Grade Point Average: ([\d.]+) \(scale ([\d.]+)\)$", line):
                sor["cumulative_gpa"], sor["gpa_scale"] = num(m[1]), num(m[2])
            else:
                sor["note"] = line
        elif section == "GRADING KEY":
            pairs = GRADEPAIR.findall(line)
            if pairs and " ".join(f"{g} = {p}" for g, p in pairs) == line:
                for g, p in pairs:
                    if g not in gk["grade_points"]: warn.append(f"grade {g!r} not in template")
                    gk["grade_points"][g] = num(p)
            elif " : " in line:
                code, _, meaning = line.partition(" : ")
                if code not in gk["codes"]: warn.append(f"grade code {code!r} not in template")
                gk["codes"][code] = meaning
            else:
                gk["notes"].append(line)

    doc["warnings"] = list(dict.fromkeys(warn))  # one copy of each, first-seen order
    inst = doc["institution"]
    inst["name"] = letterhead[0] if letterhead else None
    for h in letterhead[1:]:
        if "Registrar" in h: inst["registrar_office"] = h
        elif h == "Official Academic Transcript": doc["document_title"] = h
        else: inst["location"] = h
    return doc

if __name__ == "__main__":
    folder = Path(sys.argv[1])
    for txt in sorted(folder.glob("*.txt")):
        doc = convert(txt)
        txt.with_suffix(".common.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for w in doc["warnings"]:
            print(f"{txt.name}: {w}")
    print("done")
