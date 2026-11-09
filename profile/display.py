"""A profile as the pages show it: flattened, each residence as three fields
(country, state, city), without id_* keys.  Used by View → Profiles, the
student dialog's Profile tab and Student Rank's Profile column.

    fields({"name": "Ann", "id_x": 1, "major_residence": {"residence_city": "Pune", ...}})
        -> {"name": "Ann", "M.country": "India", "M.state": "Maharashtra", "M.city": "Pune"}

The major residence is "M" and the second one "S"; hint() explains a field name
for the pages' hover text.
"""

from __future__ import annotations

# residence_<part> -> the part's field name after its residence's prefix
RESIDENCE_PARTS = {"residence_country": "country", "residence_state": "state", "residence_city": "city"}
# A profile's residences and their field prefix; each always gives its three
# fields, null or not.
RESIDENCES = {"major_residence": "M", "second_residence": "S"}
# A residence prefix -> what it stands for.
PREFIX_MEANING = {"M": "major residence", "S": "second residence"}
# field -> what it means: the column names' hover text on every page.
HINTS = {
    "name": "The applicant's name",
    "sex": "The applicant's sex",
    "age": "The applicant's age, in years",
    "birth_date": "The applicant's date of birth (YYYY-MM-DD)",
    "institution": "The school the applicant's transcript is from",
    "program": "The applicant's program of study (their major)",
    "years_of_military_service": "How many years the applicant served in the military",
    **{f"{prefix}.{part}": f"{prefix} = {meaning}: the {part} the applicant "
                           f"{'mainly lives in' if prefix == 'M' else 'also lives in'}"
       for prefix, meaning in PREFIX_MEANING.items() for part in RESIDENCE_PARTS.values()},
}


def hint(field: str) -> str:
    """What ``field`` means: "M.state" -> "M = major residence: the state the
    applicant mainly lives in"; "" for a field it does not know."""
    return HINTS.get(field, "")


def hints(names) -> dict[str, str]:
    """{field: hint} for the ``names`` that have one (for the pages' scripts)."""
    return {n: h for n in names if (h := hint(n))}


def flatten(data: dict, prefix: str = "") -> dict[str, object]:
    """{"a": {"b": 1}} -> {"a.b": 1}; lists are shown as they are.

    A residence (a RESIDENCES key, or any dict with residence_country/state/city)
    becomes three fields, <M|S|key>.country, .state and .city; a null one (or a
    missing part) is None."""
    flat = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        short = RESIDENCES.get(name, name)
        if isinstance(value, dict) and any(k in value for k in RESIDENCE_PARTS):
            for k, part in RESIDENCE_PARTS.items():
                v = value.get(k)
                flat[f"{short}.{part}"] = None if v in (None, "") else v
            rest = {k: v for k, v in value.items() if k not in RESIDENCE_PARTS}
            flat.update(flatten(rest, f"{short}."))
        elif name in RESIDENCES and not isinstance(value, dict):
            flat.update({f"{short}.{part}": None for part in RESIDENCE_PARTS.values()})
        elif isinstance(value, dict):
            flat.update(flatten(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def fields(data: dict) -> dict[str, object]:
    """``data`` flattened, without id_* keys."""
    return {k: v for k, v in flatten(data).items() if not k.split(".")[-1].startswith("id_")}
