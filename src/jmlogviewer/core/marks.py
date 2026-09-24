"""Marks: lines or sections of a log the user flagged as important."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

MARK_COLORS = ("#0090FF", "#E5484D", "#46A758", "#FFC53D", "#8E4EC6", "#F76B15")
DEFAULT_MARK_COLOR = MARK_COLORS[0]

_ids = itertools.count(1)


@dataclass(eq=False, slots=True)
class Mark:
    """An inclusive range of lines with an optional label."""

    first: int
    last: int
    color: str = DEFAULT_MARK_COLOR
    label: str = ""
    id: int = field(default_factory=lambda: next(_ids))

    def __post_init__(self) -> None:
        self.normalize()

    def normalize(self) -> None:
        """Keep ``0 <= first <= last``."""
        if self.first > self.last:
            self.first, self.last = self.last, self.first
        self.first = max(0, self.first)

    def __contains__(self, line: object) -> bool:
        return isinstance(line, int) and self.first <= line <= self.last

    @property
    def size(self) -> int:
        return self.last - self.first + 1

    def describe_lines(self) -> str:
        """Human (1-based) line numbers, e.g. ``"12"`` or ``"12-40"``."""
        if self.first == self.last:
            return f"{self.first + 1:,}"
        return f"{self.first + 1:,}\N{EN DASH}{self.last + 1:,}"

    def to_dict(self) -> dict[str, Any]:
        return {"first": self.first, "last": self.last, "color": self.color, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mark:
        first = int(data["first"])
        return cls(
            first=first,
            last=int(data.get("last", first)),
            color=str(data.get("color") or DEFAULT_MARK_COLOR),
            label=str(data.get("label", "")),
        )


class MarkStore:
    """The marks of one file, kept sorted by position."""

    def __init__(self, marks: Iterable[Mark] = ()) -> None:
        self._marks: list[Mark] = []
        for mark in marks:
            self._insert(mark)

    def __iter__(self) -> Iterator[Mark]:
        return iter(self._marks)

    def __len__(self) -> int:
        return len(self._marks)

    def __bool__(self) -> bool:
        return bool(self._marks)

    def _insert(self, mark: Mark) -> None:
        self._marks.append(mark)
        self._marks.sort(key=lambda m: (m.first, m.last))

    def add(self, first: int, last: int, color: str = DEFAULT_MARK_COLOR, label: str = "") -> Mark:
        mark = Mark(first, last, color, label)
        self._insert(mark)
        return mark

    def remove(self, mark: Mark) -> None:
        self._marks = [m for m in self._marks if m is not mark]

    def clear(self) -> None:
        self._marks.clear()

    def replace_all(self, marks: Iterable[Mark]) -> None:
        self._marks = sorted(marks, key=lambda m: (m.first, m.last))

    def update(self, mark: Mark, *, first: int | None = None, last: int | None = None) -> None:
        """Move or resize *mark*, keeping the store sorted."""
        mark.first = mark.first if first is None else first
        mark.last = mark.last if last is None else last
        mark.normalize()
        self._marks.sort(key=lambda m: (m.first, m.last))

    def at(self, line: int) -> Mark | None:
        """The innermost mark containing *line*, if any."""
        containing = [m for m in self._marks if line in m]
        return min(containing, key=lambda m: m.size) if containing else None

    def overlapping(self, first: int, last: int) -> list[Mark]:
        """Marks intersecting the inclusive line range ``[first, last]``."""
        return [m for m in self._marks if m.first <= last and m.last >= first]

    def toggle(self, line: int, color: str = DEFAULT_MARK_COLOR) -> Mark | None:
        """Remove the mark on *line* or add a one-line mark; return the new mark."""
        existing = self.at(line)
        if existing is not None:
            self.remove(existing)
            return None
        return self.add(line, line, color)

    def next_after(self, line: int) -> Mark | None:
        return next((m for m in self._marks if m.first > line), None)

    def prev_before(self, line: int) -> Mark | None:
        before = [m for m in self._marks if m.first < line]
        return before[-1] if before else None

    def to_list(self) -> list[dict[str, Any]]:
        return [mark.to_dict() for mark in self._marks]
