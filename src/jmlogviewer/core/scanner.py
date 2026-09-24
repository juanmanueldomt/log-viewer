"""Fast, chunked, line-oriented pattern scanning.

Scanning works on blocks of whole lines decoded into one string, so the regular
expression engine runs in C over large spans and Python only does work per
*matching* line. When most lines match, splitting the block into lines is
cheaper, so the scanners switch strategy based on the hit density they observe.
"""

from __future__ import annotations

import re
import time
from array import array
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .logfile import LogFile

SCAN_CHUNK_BYTES = 256 * 1024
"""Bytes decoded and searched per step; small enough to keep the UI thread responsive."""

EMIT_INTERVAL = 0.1
"""Seconds between progress reports of a running scan."""

_DENSE_RATIO = 0.25


def new_lines(values: list[int] | None = None) -> array[int]:
    """Create a compact array of line numbers."""
    return array("I", values or ())


@dataclass(frozen=True, slots=True)
class Matcher:
    """A compiled predicate telling whether a line contains a match.

    With ``fold_case`` the pattern is lower-case and text is lower-cased before
    matching: several times faster than ``re.IGNORECASE`` for plain-text queries.
    A ``prefilter`` is a cheaper pattern matching a superset of the lines (the
    same expression without word boundaries, which defeat the regex engine's
    literal search): it finds candidate lines that ``pattern`` then confirms.
    """

    pattern: re.Pattern[str]
    fold_case: bool = False
    prefilter: re.Pattern[str] | None = None

    def matches(self, line: str) -> bool:
        """Return whether *line* (a single line) contains a match."""
        if self.fold_case:
            line = line.lower()
        if self.prefilter is not None and self.prefilter.search(line) is None:
            return False
        return self.pattern.search(line) is not None

    def matching_lines(self, text: str, first: int, *, dense: bool = False) -> array[int]:
        """Return the numbers of the lines of *text* that contain a match.

        *text* holds consecutive lines separated by ``\\n`` and its first line is
        line number *first*. A match never spans lines: a candidate crossing a
        line end is re-checked against its own line only.
        """
        if self.fold_case:
            text = text.lower()  # may change lengths, but never the newlines
        confirm = self.pattern.search
        if dense:
            return new_lines(
                [first + i for i, line in enumerate(text.split("\n")) if confirm(line)]
            )
        prefiltered = self.prefilter is not None
        search = self.prefilter.search if self.prefilter is not None else confirm

        found = new_lines()
        append = found.append
        find, rfind, count = text.find, text.rfind, text.count
        size = len(text)
        pos = 0
        line = first
        counted = 0  # position up to which newlines have been counted into `line`
        while True:
            match = search(text, pos)
            if match is None:
                break
            start = match.start()
            line_start = rfind("\n", 0, start) + 1
            line_end = find("\n", start)
            if line_end < 0:
                line_end = size
            line += count("\n", counted, line_start)
            counted = line_start
            if (not prefiltered and match.end() <= line_end) or confirm(
                text, line_start, line_end
            ) is not None:
                append(line)
            pos = line_end + 1  # a line is reported once, however many matches it has
            if pos > size:
                break
        return found


class ChunkScanner(Protocol):
    """Computes, for a block of lines, the line numbers belonging to some set."""

    def __call__(self, text: str, first: int, stop: int) -> array[int]: ...


class MatchScanner:
    """Lines containing a match of a :class:`Matcher`."""

    def __init__(self, matcher: Matcher) -> None:
        self._matcher = matcher
        self._dense = False

    def __call__(self, text: str, first: int, stop: int) -> array[int]:
        found = self._matcher.matching_lines(text, first, dense=self._dense)
        self._dense = len(found) > (stop - first) * _DENSE_RATIO
        return found


class VisibleScanner:
    """Lines that pass a filter: matching *include* (if any) and not *exclude*."""

    def __init__(self, include: Matcher | None, exclude: Matcher | None) -> None:
        self._include = MatchScanner(include) if include else None
        self._exclude = MatchScanner(exclude) if exclude else None

    def __call__(self, text: str, first: int, stop: int) -> array[int]:
        hidden = self._exclude(text, first, stop) if self._exclude else new_lines()
        if self._include is not None:
            shown = self._include(text, first, stop)
            if hidden:
                hidden_set = set(hidden)
                shown = new_lines([line for line in shown if line not in hidden_set])
            return shown
        visible = new_lines()
        previous = first
        for line in hidden:
            visible.extend(range(previous, line))
            previous = line + 1
        visible.extend(range(previous, stop))
        return visible


@dataclass(frozen=True, slots=True)
class ScanUpdate:
    """Progress of a scan: ``lines`` found in the lines scanned up to ``upto``."""

    upto: int
    lines: array[int]


def iter_blocks(
    file: LogFile, first: int, stop: int, chunk_bytes: int = SCAN_CHUNK_BYTES
) -> Iterator[tuple[int, int, str]]:
    """Yield ``(first, stop, text)`` blocks of about *chunk_bytes* covering the lines."""
    if first >= stop:
        return
    with file.reader() as reader:
        start = first
        while start < stop:
            end = file.block_end(start, stop, chunk_bytes)
            yield start, end, reader.read_block(start, end)
            start = end


def scan_lines(
    file: LogFile,
    first: int,
    stop: int,
    scanner: ChunkScanner,
    *,
    chunk_bytes: int = SCAN_CHUNK_BYTES,
    interval: float = EMIT_INTERVAL,
) -> Generator[ScanUpdate | None]:
    """Scan lines ``[first, stop)``, a block per step.

    Found lines are reported in batches about every *interval* seconds (and at
    the end); other steps yield ``None`` so a scheduler can interleave work.
    """
    pending = new_lines()
    last_emit = time.monotonic()
    upto = first
    for start, end, text in iter_blocks(file, first, stop, chunk_bytes):
        pending.extend(scanner(text, start, end))
        upto = end
        now = time.monotonic()
        if now - last_emit >= interval:
            yield ScanUpdate(upto, pending)
            pending = new_lines()
            last_emit = now
        else:
            yield None
    yield ScanUpdate(max(upto, stop), pending)
