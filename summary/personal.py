"""Profile: the students' profiles, as sub-columns of chosen fields.

Every <work-folder>/personal/*.json with a "name" gives one row; its fields are as
View → Profiles shows them (profile.display.fields: program, age, M.state, ...).
The table shows one sub-column under "Profile" per field of ``show`` (default
institution and program), in that order; rank() gives the first one.

A field has one of two kinds of filter:

    range  RANGE_FIELDS (age, birth_date, years_of_military_service): from/to,
           both ends included; birth_date compares ISO dates (YYYY-MM-DD), the
           others numbers
    list   every other field: a list[str]; only the students whose value is in
           it (ignoring case) are kept; no list (None or empty) keeps them all

``ranges`` is {field: (low, high)} (either end may be None) and ``values`` is
{field: list[str]}. A filter never drops a profile: a value it does not keep
is None, so only that field's sub-column cell is blank and the other fields
keep their values. Only a shown field filters: the others' filters are dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from profile import display, profile_load
from summary import ABS, RankItem

TITLE = "Profile"
DESCRIPTION = "The applicant's personal information"
DEFAULT_SHOW = ("institution", "program")
# list field -> its list when none is given (the Config dialog starts with it)
DEFAULT_VALUES = {"sex": ["M", "F"]}
# range field -> "number" or "date"; every other field is a string list
RANGE_FIELDS = {"age": "number", "birth_date": "date", "years_of_military_service": "number"}
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

Bound = float | str | None  # a number, or an ISO date for a date field


@dataclass
class Field:
    """A profile field as the Profile dialog offers it."""
    name: str
    kind: str  # "number", "date" (both range filters) or "list"
    values: dict[str, int] = dc_field(default_factory=dict)  # list: value -> how many profiles have it
    blanks: int = 0  # how many profiles have no value

    @property
    def hint(self) -> str:
        return display.hint(self.name)


def is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def ordered(kind: str, value: object) -> Bound:
    """A range field's value as it compares: a number, an ISO date; None if it is
    neither (it then matches no range)."""
    if kind == "date":
        return value[:10] if isinstance(value, str) and ISO_DATE.fullmatch(value[:10]) else None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _named(cfg: dict) -> list[tuple[str, dict[str, object]]]:
    """(name, its fields) per profile with a name."""
    rows = []
    for data in profile_load.load_cfg(cfg).values():
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            rows.append((name.strip(), display.fields(data)))
    return rows


def profile_fields(cfg: dict) -> dict[str, Field]:
    """Every field the named profiles have, in first-seen order, except "name";
    a list field with its values (sorted) and counts."""
    rows = [fields for _, fields in _named(cfg)]
    names = dict.fromkeys(k for fields in rows for k in fields)
    names.pop("name", None)
    out = {}
    for name in names:
        f = Field(name, RANGE_FIELDS.get(name, "list"))
        counts: dict[str, int] = {}
        for fields in rows:
            value = fields.get(name)
            if is_blank(value):
                f.blanks += 1
            elif f.kind == "list":
                counts[str(value)] = counts.get(str(value), 0) + 1
        f.values = dict(sorted(counts.items(), key=lambda kv: kv[0].lower()))
        out[name] = f
    return out


def _fmt(kind: str, value: Bound) -> str:
    return value if kind == "date" else f"{value:g}"


def range_label(kind: str, low: Bound, high: Bound) -> str:
    """20, 30 -> "20–30"; 20, None -> "≥ 20"; dates as they are."""
    if low is not None and high is not None:
        return f"{_fmt(kind, low)}–{_fmt(kind, high)}"
    if low is not None:
        return f"≥ {_fmt(kind, low)}"
    return f"≤ {_fmt(kind, high)}" if high is not None else ""


class Profile(ABS):
    def __init__(self, cfg: dict, show: list[str] | tuple[str, ...] = DEFAULT_SHOW,
                 ranges: dict[str, tuple[Bound, Bound]] | None = None,
                 values: dict[str, list[str]] | None = None):
        show = list(dict.fromkeys(show))
        if not show:
            raise ValueError("the Profile column needs a field to show")
        self.ranges: dict[str, tuple[Bound, Bound]] = {}
        for name, (low, high) in (ranges or {}).items():
            kind = RANGE_FIELDS.get(name)
            if kind is None:
                raise ValueError(f"{name!r} has no range filter; one of {', '.join(RANGE_FIELDS)}")
            for end in (low, high):
                if end is not None and (ordered(kind, end) is None
                                        or (kind == "date") != isinstance(end, str)):
                    raise ValueError(f"{name} range end {end!r} is not a {kind}")
            if low is not None and high is not None and low > high:
                raise ValueError(f"{name} range {low}–{high}: the low end is above the high end")
            if low is not None or high is not None:
                self.ranges[name] = (low, high)
        self.values = {name: list(v) for name, v in (values or {}).items() if v}
        for name in self.values:
            if name in RANGE_FIELDS:
                raise ValueError(f"{name!r} has a range filter, not a list")
        self.cfg, self.show = cfg, show
        # only a shown field filters
        self.ranges = {n: r for n, r in self.ranges.items() if n in show}
        self.values = {n: v for n, v in self.values.items() if n in show}
        self.title = TITLE
        self.description = DESCRIPTION
        self.folded_title = "/".join(show)

    def _value(self, name: str, fields: dict[str, object]) -> object:
        """``name``'s value, or None when its filter does not keep it."""
        value = fields.get(name)
        if is_blank(value):
            return None
        if name in self.ranges:
            low, high = self.ranges[name]
            v = ordered(RANGE_FIELDS[name], value)
            if v is None or (low is not None and v < low) or (high is not None and v > high):
                return None
        if name in self.values:  # a list field: in the list, ignoring case
            if str(value).strip().casefold() not in {w.strip().casefold() for w in self.values[name]}:
                return None
        return value

    def hint(self, sub_title: str) -> str:
        return display.hint(sub_title)

    def rank(self) -> list[RankItem]:
        return [RankItem(name, self._value(self.show[0], fields)) for name, fields in _named(self.cfg)]

    def columns(self) -> list[tuple[str, list[RankItem]]]:
        """One sub-column per shown field; a value its filter does not keep is None."""
        rows = _named(self.cfg)
        return [(f, [RankItem(name, self._value(f, fields)) for name, fields in rows]) for f in self.show]

    def labels(self) -> list[str]:
        """One line per filter for the Config table: "age 20–30", "sex: F, M"."""
        out = [f"{name} {range_label(RANGE_FIELDS[name], low, high)}"
               for name, (low, high) in self.ranges.items()]
        out += [f"{name}: {', '.join(wanted)}" for name, wanted in self.values.items()]
        return out
