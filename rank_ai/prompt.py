"""The prompt: what the table can be filtered by, its current filters, the
user's request word for word, and the answer format.

Nothing here knows what a user will ask. The columns, fields, meanings and
known values all come from the objects' filter_specs() at request time, and
the request is pasted in unchanged; the LLM does the understanding.
"""

from __future__ import annotations

import json

from summary import Filter, FilterSpec

MAX_VALUES = 300  # known values listed per field; the rest are summarised as "and N more"

INTRO = """You set the filters of a student ranking table from a request its user typed.

The table has one row per student and the columns below. Each column lists the fields it can be filtered by, with the field's op:
  op "in": keep only the students whose value is one of "values"; copy each value exactly as the field's known values spell it.
  op "range": keep only the students whose value is from "min" to "max", both included; leave out "min" or "max" for no bound on that end. When the request excludes the end value (words like "more than", "older than", "under") and the field holds whole numbers, move the bound one step so that value is not included: "more than 5" is "min": 6.
  op "is": a setting, not a filter; "value" is exactly one of its known values.
A field with no filter keeps every student."""

RULES = """Rules:
1. Answer with the complete list of filters the table should apply after the request: copy each current filter the request does not change, and change or leave out the ones it asks to change or remove.
2. A condition the request states on a field replaces that field's current filter: the field's new filter is exactly what the request asks for, even when the current filter is stricter or already fits the request. Current filters are the previous choice, not a limit on the new one. Combine a condition with that field's current filter only when the request itself says to (such as "also" or "of those").
3. Use only the columns, fields and ops listed above. Use your own knowledge to map the user's words onto a field's known values (a broad term onto every known value it covers), but pick values only from that field's known values.
4. If part of the request cannot be expressed with these fields, do not guess a filter for it: describe that part in "unsupported".
5. "explanation": one or two sentences telling the user which filters you chose and why, so they can check them."""

FORMAT = """Reply with only one JSON object, between a line BEGIN and a line END, like this:
BEGIN
{"filters": [{"column": "<column>", "field": "<field>", "op": "in", "values": ["<value>", "<value>"]}, {"column": "<column>", "field": "<field>", "op": "range", "min": 1, "max": 2}, {"column": "<column>", "field": "<field>", "op": "is", "value": "<value>"}], "explanation": "<sentence>", "unsupported": []}
END"""


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def field_line(spec: FilterSpec) -> str:
    """One field as the prompt lists it."""
    if spec.op == "range":
        bounds = ("" if spec.low is None and spec.high is None else
                  f" from {'' if spec.low is None else f'{spec.low:g}'}"
                  f" to {'any' if spec.high is None else f'{spec.high:g}'}")
        kind = "date YYYY-MM-DD" if spec.kind == "date" else f"number{bounds}"
        return f"- {spec.field} (range, {kind}): {spec.meaning}"
    values = spec.values or []
    shown = ", ".join(_q(v) for v in values[:MAX_VALUES])
    more = f", and {len(values) - MAX_VALUES} more" if len(values) > MAX_VALUES else ""
    known = f"known values: {shown}{more}" if values else "no known values yet"
    return f"- {spec.field} ({spec.op}): {spec.meaning}; {known}"


def catalog(specs: list[FilterSpec], descriptions: dict[str, str]) -> str:
    """Every column and its fields, in the specs' order."""
    lines: list[str] = []
    seen: set[str] = set()
    for spec in specs:
        if spec.column not in seen:
            seen.add(spec.column)
            about = descriptions.get(spec.column)
            lines += ["", f"COLUMN {spec.column}" + (f": {about}" if about else "")]
        lines.append(field_line(spec))
    return "\n".join(lines).strip()


def build(question: str, specs: list[FilterSpec], current: list[Filter],
          descriptions: dict[str, str] | None = None, today: str = "") -> str:
    """The whole prompt for ``question``, which goes in as it is."""
    parts = [INTRO, catalog(specs, descriptions or {})]
    if today:
        parts.append(f"Today is {today}.")
    parts.append("CURRENT FILTERS (what the table applies now):\n"
                 + json.dumps([f.to_dict() for f in current], ensure_ascii=False))
    parts.append("REQUEST:\n" + question.strip())
    parts += [RULES, FORMAT]
    return "\n\n".join(parts)


def repair(prompt: str, reply: str, problems: list[str]) -> str:
    """The prompt again, with the last answer and what was wrong with it."""
    listed = "\n".join(f"- {p}" for p in problems)
    return (f"{prompt}\n\nYOUR LAST ANSWER:\n{reply.strip()[:4000]}\n\n"
            f"It had these problems:\n{listed}\n\n"
            "Answer the same request again, fixing them, in the same format.")
