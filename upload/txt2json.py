"""Convert pdftotext -layout transcripts to JSON, one file per txt."""
import json, re, sys
from pathlib import Path

def snake(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

def num(s):
    return float(s) if "." in s else int(s)

FOOTER = re.compile(r"^.+ — page \d+$")
YEAR = re.compile(r"^YEAR (\d+): (.+)$")
TERM = re.compile(r"^(\w+) (Semester|Quarter|Term) (\d{4})$")
HEADER = re.compile(r"^Course\s+Title\s+Grade\s+(.+?)\s+Points$")
ROW = re.compile(r"^(?P<code>\S+(?: \S+)*?)\s{2,}(?P<title>.+?)\s{2,}(?P<grade>\S+)\s+(?P<credits>[\d.]+)\s+(?P<points>[\d.]+)$")
TSUM = re.compile(r"^(?P<label>.+?): (?P<cr>[\d.]+) Term GPA: (?P<gpa>[\d.]+)(?: Cumulative (?P=label): (?P<ccr>[\d.]+) Cumulative GPA: (?P<cgpa>[\d.]+))?$")
GRADEPAIR = re.compile(r"(\S+) = ([\d.]+)")

def parse(path):
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()]
    doc = {"source_file": path.name.replace(".txt", ".pdf")}
    header, student, years, summary, key = [], {}, [], {}, {"grade_points": {}, "codes": {}, "notes": []}
    unparsed, section, term, credit_unit = [], "header", None, None
    for l in lines:
        if not l or FOOTER.match(l):
            continue
        if l == "STUDENT RECORD": section = "student"; continue
        if l == "SUMMARY OF RECORD": section = "summary"; continue
        if l == "GRADING KEY": section = "key"; continue
        if m := YEAR.match(l):
            section = "years"; years.append({"year": int(m[1]), "label": m[2], "terms": []}); continue
        if section == "header" or (section == "years" and l.isupper() is False and l in header):
            if l not in header: header.append(l)
            continue
        if section == "student":
            k, _, v = l.partition(": "); student[snake(k)] = v; continue
        if section == "years":
            if l in header: continue  # page-2 repeat of letterhead
            if m := TERM.match(l):
                term = {"term": l, "season": m[1], "period_type": m[2].lower(), "year": int(m[3]), "courses": []}
                years[-1]["terms"].append(term); continue
            if m := HEADER.match(l):
                credit_unit = credit_unit or m[1]; continue
            if m := ROW.match(l):
                term["courses"].append({"code": m["code"], "title": m["title"], "grade": m["grade"],
                                        "credits": num(m["credits"]), "points": num(m["points"])}); continue
            if m := TSUM.match(l):
                s = {"credits": num(m["cr"]), "term_gpa": num(m["gpa"])}
                if m["ccr"]: s |= {"cumulative_credits": num(m["ccr"]), "cumulative_gpa": num(m["cgpa"])}
                term["summary"] = s; continue
        if section == "summary":
            if m := re.match(r"^Total (.+) Earned: ([\d.]+)$", l):
                summary["total_credits_earned"] = num(m[2]); continue
            if m := re.match(r"^Cumulative Grade Point Average: ([\d.]+) \(scale ([\d.]+)\)$", l):
                summary |= {"cumulative_gpa": num(m[1]), "gpa_scale": num(m[2])}; continue
            summary["note"] = l; continue
        if section == "key":
            pairs = GRADEPAIR.findall(l)
            if pairs and " ".join(f"{g} = {p}" for g, p in pairs) == l:
                key["grade_points"] |= {g: num(p) for g, p in pairs}; continue
            if " : " in l:
                k, _, v = l.partition(" : "); key["codes"][k] = v; continue
            key["notes"].append(l); continue
        unparsed.append(l)
    # letterhead: institution, optional location, registrar office, document title
    doc["institution"] = header[0]
    for h in header[1:]:
        if "Registrar" in h: doc["registrar_office"] = h
        elif h == "Official Academic Transcript": doc["document_title"] = h
        else: doc["location"] = h
    doc["credit_unit"] = credit_unit
    doc["student"] = student
    doc["years"] = years
    doc["summary_of_record"] = summary
    doc["grading_key"] = {k: v for k, v in key.items() if v}
    if unparsed: doc["unparsed_lines"] = unparsed
    return doc

if __name__ == "__main__":
    folder = Path(sys.argv[1])
    for txt in sorted(folder.glob("*.txt")):
        d = parse(txt)
        txt.with_suffix(".json").write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if "unparsed_lines" in d:
            print("UNPARSED", txt.name, d["unparsed_lines"][:3])
    print("done")
