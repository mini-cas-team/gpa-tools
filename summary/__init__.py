"""summary: browser_view's View → Summary table, one column per ranked object.

Each column comes from a subclass of ABS, whose rank() returns a list of
RankItem(name, order). The table has a Name column (the union of the student
names of every upload type, summary.names.upload_names) and one column per
object, showing each name's ``order``: None when rank() has no such name.
An object can instead give several sub-columns under its title by overriding
columns() (Transcript: STEM GPA, ALL GPA, STEM CRS %).

    from summary import table
    from summary.names import upload_names
    from summary.transcript import Transcript
    names = upload_names(cfg)
    columns = table([Transcript(cfg, "jev", "STEM")], names)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RankItem:
    name: str      # a student's name; matched exactly across objects
    order: object  # the value the Summary table shows; None shows as "–"


class ABS(ABC):
    """An object the Summary table shows as one column."""

    title: str = ""  # the column heading
    folded_title: str = ""  # with sub-columns: the heading of them folded into one ("STEM/ALL/%")
    description: str = ""  # what the column means: its heading's hover text

    @abstractmethod
    def rank(self) -> list[RankItem]:
        """One RankItem per student this object knows about."""

    def columns(self) -> list[tuple[str, list[RankItem]]] | None:
        """(sub-column title, its items) per sub-column, shown under ``title``;
        None (the default): one column, ``title``, of rank()."""
        return None

    def hint(self, sub_title: str) -> str:
        """What a sub-column means: its heading's hover text; "" for none."""
        return ""



def in_range(value: object, bounds: tuple | None) -> bool:
    """``value`` is in the inclusive (low, high) ``bounds`` (either end None: no
    bound); no bounds: always; a None value: never when there are bounds."""
    if bounds is None:
        return True
    low, high = bounds
    return value is not None and (low is None or value >= low) and (high is None or value <= high)


def filtered(values: dict[str, object], ranges: dict[str, tuple]) -> dict[str, object]:
    """{source: value} with each value outside its source's range as None: a
    filter blanks only its own sub-column's cell, never the record."""
    return {source: value if in_range(value, ranges.get(source)) else None
            for source, value in values.items()}


@dataclass
class Column:
    title: str
    orders: dict[str, object]  # name -> order
    group: str = ""  # the object's title when this is one of its sub-columns
    folded: str = ""  # its group's folded heading (ABS.folded_title)
    hint: str = ""  # its hover text: the sub-column's ABS.hint, else the object's description
    description: str = ""  # its object's description (ABS.description)

    @property
    def numeric(self) -> bool:
        """Every order is a number (or None): the column sorts as numbers, else as text."""
        return all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))
                   for v in self.orders.values())


def table(objects: list[ABS], names: list[str]) -> list[Column]:
    """One Column per object, in order, with an order for every one of ``names``
    (the table's rows): None where the object's rank() has no item of that name.

    Items whose name is not in ``names`` are dropped; a name ranked twice keeps
    its first order.

    Each object fills only its own columns: a name its filter leaves out is
    None there and keeps its values in the other objects' columns."""
    def orders(items: list[RankItem]) -> dict[str, object]:
        found: dict[str, object] = {}
        for item in items:
            found.setdefault(item.name, item.order)
        return {name: found.get(name) for name in names}

    columns = []
    for obj in objects:
        title = obj.title or type(obj).__name__
        parts = obj.columns()
        if parts is None:
            columns.append(Column(title, orders(obj.rank()), hint=obj.description,
                                  description=obj.description))
        else:
            folded = obj.folded_title or "/".join(sub for sub, _ in parts)
            columns.extend(Column(sub, orders(items), group=title, folded=folded,
                                  hint=obj.hint(sub), description=obj.description)
                           for sub, items in parts)
    return columns


@dataclass
class Group:
    """One header cell of the table: an object's single column (``title`` "",
    one column) or its sub-columns under ``title``, which the page can fold into
    one column, ``folded``, at body index ``fold_index``."""
    title: str
    cols: list[tuple[int, Column]]  # (body cell index, column)
    folded: str = ""
    fold_index: int | None = None
    description: str = ""  # the object's description: the group heading's hover text


def groups(columns: list[Column]) -> list[Group]:
    """The table's header, one Group per run of consecutive columns of one object.
    Body indices count from 1 (0 is the Name column); each group of sub-columns
    is followed by its folded cell, which takes an index of its own."""
    out: list[Group] = []
    index = 0
    for column in columns:
        if column.group and out and out[-1].title == column.group and out[-1].fold_index is None:
            out[-1].cols.append((index := index + 1, column))
            continue
        if out and out[-1].title and out[-1].fold_index is None:
            out[-1].fold_index = index = index + 1
        out.append(Group(column.group, [(index := index + 1, column)], column.folded,
                         description=column.description))
    if out and out[-1].title and out[-1].fold_index is None:
        out[-1].fold_index = index + 1
    return out
