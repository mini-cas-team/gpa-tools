"""Reading an LLM's answer: the JSON object out of its reply, then each filter
checked against the specs it was offered.

Any provider's reply goes through here: Ollama's schema-constrained JSON, or a
chat site's free text with the object between BEGIN and END (and maybe a
preamble or a code fence around it).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from summary import OPS, Filter, FilterSpec

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Ollama's format: the answer's shape (min/max are numbers or ISO dates, so untyped)
SCHEMA = {
    "type": "object",
    "properties": {
        "filters": {"type": "array", "items": {
            "type": "object",
            "properties": {"column": {"type": "string"}, "field": {"type": "string"},
                           "op": {"type": "string", "enum": list(OPS)},
                           "values": {"type": "array", "items": {"type": "string"}},
                           "value": {"type": "string"}, "min": {}, "max": {}},
            "required": ["column", "field", "op"]}},
        "explanation": {"type": "string"},
        "unsupported": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["filters", "explanation", "unsupported"],
}


class NoAnswer(ValueError):
    """The reply had no answer object in it."""


def extract(reply: str) -> dict:
    """The last JSON object with a "filters" key in ``reply`` (after BEGIN when
    there is one: a model that corrects itself ends with the right one)."""
    text = reply.replace("**", "")
    start = text.rfind("BEGIN")
    start = 0 if start < 0 else start
    decoder = json.JSONDecoder()
    found = None
    for match in re.finditer(r"\{", text[start:]):
        try:
            value, _ = decoder.raw_decode(text, start + match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "filters" in value:
            found = value
    if found is None:
        clip = " ".join(reply.split())[:200]
        raise NoAnswer(f"no JSON object with \"filters\" in the reply: {clip!r}")
    return found


@dataclass
class Checked:
    filters: list[Filter] = field(default_factory=list)  # the usable ones
    problems: list[str] = field(default_factory=list)  # what was wrong (told to the LLM on a retry)
    explanation: str = ""
    unsupported: list[str] = field(default_factory=list)


def _bound(spec: FilterSpec, raw: object) -> tuple[float | str | None, str | None]:
    """(the bound, None) or (None, the problem)."""
    if raw is None or raw == "":
        return None, None
    if spec.kind == "date":
        text = str(raw).strip()[:10]
        return (text, None) if ISO_DATE.fullmatch(text) else (None, f"{raw!r} is not a YYYY-MM-DD date")
    if isinstance(raw, bool):
        return None, f"{raw!r} is not a number"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"{raw!r} is not a number"
    if (spec.low is not None and value < spec.low) or (spec.high is not None and value > spec.high):
        lo = "" if spec.low is None else f"{spec.low:g}"
        hi = "any" if spec.high is None else f"{spec.high:g}"
        return None, f"{value:g} is outside {lo}–{hi}"
    return value, None


def _known(spec: FilterSpec, wanted: list) -> tuple[list[str], list[str]]:
    """(the wanted values as the known values spell them, the unknown ones)."""
    known = {v.strip().casefold(): v for v in spec.values or []}
    got, unknown = [], []
    for raw in wanted:
        text = str(raw).strip()
        if text.casefold() in known:
            got.append(known[text.casefold()])
        elif text:
            unknown.append(text)
    return list(dict.fromkeys(got)), unknown


def _clip(values: list[str], n: int = 12) -> str:
    shown = ", ".join(values[:n])
    return shown + (f", ... ({len(values)} in all)" if len(values) > n else "")


def check(data: dict, specs: list[FilterSpec]) -> Checked:
    """The answer's filters that fit ``specs`` (spelled as the specs spell
    them), and a problem for each one that does not. The op is the spec's:
    "values" and "value" are read either way."""
    out = Checked()
    out.explanation = data.get("explanation") if isinstance(data.get("explanation"), str) else ""
    unsupported = data.get("unsupported")
    if isinstance(unsupported, str):
        unsupported = [unsupported]
    out.unsupported = [str(u) for u in unsupported or [] if str(u).strip()] if isinstance(unsupported, list) else []
    raw_filters = data.get("filters")
    if not isinstance(raw_filters, list):
        out.problems.append('"filters" must be a list')
        return out
    by_key = {(s.column.casefold(), s.field.casefold()): s for s in specs}
    columns = list(dict.fromkeys(s.column for s in specs))
    merged: dict[tuple[str, str], Filter] = {}
    for raw in raw_filters:
        if not isinstance(raw, dict):
            out.problems.append(f"a filter is not an object: {raw!r}")
            continue
        column, name = str(raw.get("column") or "").strip(), str(raw.get("field") or "").strip()
        spec = by_key.get((column.casefold(), name.casefold()))
        if spec is None:
            if column.casefold() not in {c.casefold() for c in columns}:
                out.problems.append(f"there is no column {column!r} (columns: {', '.join(columns)})")
            else:
                fields = [s.field for s in specs if s.column.casefold() == column.casefold()]
                out.problems.append(f"column {column} has no field {name!r} (fields: {', '.join(fields)})")
            continue
        label = f"{spec.column} {spec.field}"
        values = raw.get("values")
        if not isinstance(values, list):
            values = [] if values is None else [values]
        if raw.get("value") not in (None, ""):
            values = [raw["value"], *values]
        if spec.op == "range":
            bounds = []
            for end in ("min", "max"):
                value, problem = _bound(spec, raw.get(end))
                if problem:
                    out.problems.append(f"{label} {end}: {problem}")
                bounds.append(value)
            low, high = bounds
            if low is None and high is None:
                if not any(raw.get(end) not in (None, "") for end in ("min", "max")):
                    out.problems.append(f"{label} is a range: give \"min\", \"max\" or both")
                continue
            if low is not None and high is not None and low > high:
                low, high = high, low
            merged[(spec.column, spec.field)] = Filter(spec.column, spec.field, "range", min=low, max=high)
            continue
        got, unknown = _known(spec, values)
        if unknown:
            out.problems.append(f"{label} has no value {_clip([repr(u) for u in unknown])}; "
                                f"its known values are {_clip(spec.values or [])}")
        if spec.op == "is":
            if not got:
                if not unknown:
                    out.problems.append(f"{label} is a setting: give one \"value\"")
                continue
            merged[(spec.column, spec.field)] = Filter(spec.column, spec.field, "is", got[:1])
            continue
        if not got:
            continue  # nothing usable: no filter on this field
        old = merged.get((spec.column, spec.field))
        if old is not None:  # the same field twice: both lists
            got = list(dict.fromkeys([*(old.values or []), *got]))
        merged[(spec.column, spec.field)] = Filter(spec.column, spec.field, "in", got)
    out.filters = list(merged.values())
    return out
