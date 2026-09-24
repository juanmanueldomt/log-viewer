"""Compact sorted sets of line numbers, and the mapping between view rows and lines."""

from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Iterator
from typing import Protocol


class LineSet:
    """A sorted set of line numbers stored compactly (4 bytes per line)."""

    __slots__ = ("_lines",)

    def __init__(self, lines: Iterable[int] = ()) -> None:
        self._lines = array("I", lines)

    def __len__(self) -> int:
        return len(self._lines)

    def __iter__(self) -> Iterator[int]:
        return iter(self._lines)

    def __getitem__(self, index: int) -> int:
        return self._lines[index]

    def __contains__(self, line: object) -> bool:
        if not isinstance(line, int):
            return False
        index = bisect_left(self._lines, line)
        return index < len(self._lines) and self._lines[index] == line

    def __repr__(self) -> str:
        return f"LineSet({self._lines.tolist()!r})"

    def extend(self, lines: Iterable[int]) -> None:
        """Append lines that are all greater than the lines already present."""
        self._lines.extend(lines)

    def truncate(self, line: int) -> None:
        """Remove every line greater than or equal to *line*."""
        del self._lines[bisect_left(self._lines, line) :]

    def rank(self, line: int) -> int:
        """Number of lines in the set smaller than *line*."""
        return bisect_left(self._lines, line)

    def slice(self, start: int, stop: int) -> list[int]:
        return self._lines[start:stop].tolist()

    def next_after(self, line: int) -> int | None:
        index = bisect_right(self._lines, line)
        return self._lines[index] if index < len(self._lines) else None

    def prev_before(self, line: int) -> int | None:
        index = bisect_left(self._lines, line)
        return self._lines[index - 1] if index > 0 else None

    def any_between(self, first: int, last: int) -> bool:
        """Whether some line lies in the inclusive range ``[first, last]``."""
        index = bisect_left(self._lines, first)
        return index < len(self._lines) and self._lines[index] <= last


class RowMap(Protocol):
    """Which file line is displayed on each row of the view."""

    @property
    def filtered(self) -> bool: ...

    def __len__(self) -> int: ...

    def line_at(self, row: int) -> int: ...

    def lines(self, first_row: int, stop_row: int) -> list[int]: ...

    def row_of(self, line: int) -> int | None:
        """Row showing *line*, or ``None`` when the line is hidden."""
        ...

    def nearest_row(self, line: int) -> int:
        """Row of *line*, else of the closest following visible line (clamped)."""
        ...


class AllRows:
    """No filter: row *n* shows line *n*."""

    filtered = False

    def __init__(self, count: Callable[[], int]) -> None:
        self._count = count

    def __len__(self) -> int:
        return self._count()

    def line_at(self, row: int) -> int:
        return row

    def lines(self, first_row: int, stop_row: int) -> list[int]:
        return list(range(first_row, min(stop_row, len(self))))

    def row_of(self, line: int) -> int | None:
        return line if 0 <= line < len(self) else None

    def nearest_row(self, line: int) -> int:
        return max(0, min(line, len(self) - 1))


class FilteredRows:
    """Only the lines of a :class:`LineSet` are shown."""

    filtered = True

    def __init__(self, lines: LineSet) -> None:
        self._lines = lines

    def __len__(self) -> int:
        return len(self._lines)

    def line_at(self, row: int) -> int:
        return self._lines[row]

    def lines(self, first_row: int, stop_row: int) -> list[int]:
        return self._lines.slice(first_row, stop_row)

    def row_of(self, line: int) -> int | None:
        row = self._lines.rank(line)
        return row if row < len(self._lines) and self._lines[row] == line else None

    def nearest_row(self, line: int) -> int:
        return max(0, min(self._lines.rank(line), len(self._lines) - 1))
