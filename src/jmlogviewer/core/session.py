"""Everything about the log being viewed, independent of any user interface.

A :class:`LogSession` owns the open :class:`~.logfile.LogFile` and keeps derived
data up to date in the background while the file is indexed or keeps growing
(tail): search hits, the hits of each highlight rule and the rows left visible
by filters. Listeners are told what changed through :class:`Change` flags.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from enum import Flag, auto
from functools import partial

from .lineset import AllRows, FilteredRows, LineSet, RowMap
from .logfile import FileChange, IndexBatch, LogFile, index_lines
from .marks import DEFAULT_MARK_COLOR, Mark, MarkStore
from .query import EMPTY_LINE_SOURCE, Query, QueryError, combine
from .rules import Rule, RuleAction
from .scanner import ChunkScanner, Matcher, MatchScanner, ScanUpdate, VisibleScanner, scan_lines
from .tasks import Executor, JobHandle, Priority

log = logging.getLogger(__name__)


class Change(Flag):
    """What changed in a session."""

    NONE = 0
    FILE = auto()  # a file was opened, reloaded or closed
    LINES = auto()  # lines were appended (loading or tail)
    ROWS = auto()  # the visible rows changed (filters)
    SEARCH = auto()  # the search or its results changed
    RULES = auto()  # highlight rules or their hits changed
    MARKS = auto()  # marks changed
    STATUS = auto()  # background activity or the status message changed
    ALL = FILE | LINES | ROWS | SEARCH | RULES | MARKS | STATUS


@dataclass(frozen=True, slots=True)
class Activity:
    """A background task worth showing to the user."""

    label: str
    progress: float


@dataclass(frozen=True, slots=True)
class Highlighter:
    """An enabled highlight rule, compiled for use on visible lines."""

    index: int
    rule: Rule
    pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class Hit:
    """Result of navigating to the next/previous line of a set."""

    line: int
    wrapped: bool


class LineScan:
    """A set of lines of a file kept up to date as the file grows.

    Lines are examined in the background and results arrive progressively. A
    last line that is not newline-terminated may still change, so it is scanned
    again when more data arrives.
    """

    def __init__(
        self,
        file: LogFile,
        executor: Executor,
        make_scanner: Callable[[], ChunkScanner],
        priority: Priority,
        on_change: Callable[[], None],
    ) -> None:
        self.lines = LineSet()
        self.scanned = 0  # lines [0, scanned) have been examined
        self.error: str | None = None
        self._file = file
        self._executor = executor
        self._make_scanner = make_scanner
        self._priority = priority
        self._on_change = on_change
        self._stable = 0  # lines [0, stable) never need scanning again
        self._scanned_size = -1  # file size covered by the last finished scan
        self._job: JobHandle | None = None

    @property
    def running(self) -> bool:
        return self._job is not None

    @property
    def complete(self) -> bool:
        return self._job is None and self._scanned_size == self._file.indexed_size

    @property
    def progress(self) -> float:
        total = self._file.line_count
        return 1.0 if total == 0 else min(1.0, self.scanned / total)

    def cancel(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self._job = None

    def update(self) -> None:
        """Scan whatever has not been scanned yet; a no-op while a scan runs."""
        file = self._file
        if self._job is not None or self.error or self._scanned_size == file.indexed_size:
            return
        first, stop = self._stable, file.line_count
        stable, size = file.complete_line_count, file.indexed_size
        scanner = self._make_scanner()
        restarted = False

        def work() -> Generator[ScanUpdate | None]:
            return scan_lines(file, first, stop, scanner)

        def on_progress(update: ScanUpdate) -> None:
            nonlocal restarted
            if not restarted:  # drop results for lines being re-scanned
                self.lines.truncate(first)
                restarted = True
            self.lines.extend(update.lines)
            self.scanned = update.upto
            self._on_change()

        def on_done(_: None) -> None:
            self._job = None
            self._stable = min(stop, stable)
            self._scanned_size = size
            self._on_change()
            self.update()  # catch up with lines appended meanwhile

        def on_error(error: BaseException) -> None:
            log.error("Scan failed", exc_info=error)
            self._job = None
            self.error = str(error) or type(error).__name__
            self._on_change()

        self._job = self._executor.submit(
            work,
            priority=self._priority,
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
        )


def step(
    lines: LineSet, current: int, *, backwards: bool, visible: Callable[[int], bool]
) -> Hit | None:
    """Find the next (or previous) visible line of *lines* after *current*, wrapping around."""
    if not len(lines):
        return None
    advance = lines.prev_before if backwards else lines.next_after
    candidate = advance(current)
    wrapped = False
    while True:
        if candidate is None:
            if wrapped:
                return None
            wrapped = True
            candidate = lines[len(lines) - 1] if backwards else lines[0]
        if wrapped and (candidate < current if backwards else candidate > current):
            return None  # came full circle
        if visible(candidate):
            return Hit(candidate, wrapped)
        candidate = advance(candidate)


class LogSession:
    """The state of the log being viewed. All methods must be called from one thread."""

    def __init__(
        self, executor: Executor, rules: Iterable[Rule] = (), *, hide_empty: bool = False
    ) -> None:
        self.file: LogFile | None = None
        self.marks = MarkStore()
        self.message = ""
        self._executor = executor
        self._listeners: list[Callable[[Change], None]] = []
        self._index_job: JobHandle | None = None
        self._index_range = (0, 0)
        self._missing = False

        self._rules: list[Rule] = list(rules)
        self._rule_errors: dict[int, str] = {}
        self._highlighters: list[Highlighter] = []
        self._rule_scans: dict[Query, LineScan] = {}
        self._hide_empty = hide_empty
        self._hide_queries: tuple[Query, ...] = ()
        self._exclude: Matcher | None = None

        self._search_query: Query | None = None
        self._search_pattern: re.Pattern[str] | None = None
        self._search_matcher: Matcher | None = None
        self._search_scan: LineScan | None = None
        self._filter_to_search = False
        self._filter_scan: LineScan | None = None
        self._rows: RowMap = AllRows(lambda: self.line_count)

        self._compile_rules()

    # -- notifications --------------------------------------------------------

    def subscribe(self, listener: Callable[[Change], None]) -> Callable[[], None]:
        """Call *listener* on every change; returns a function that unsubscribes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify(self, change: Change) -> None:
        for listener in list(self._listeners):
            listener(change)

    # -- file -----------------------------------------------------------------

    @property
    def line_count(self) -> int:
        return self.file.line_count if self.file else 0

    @property
    def loading(self) -> bool:
        return self._index_job is not None

    def open(self, path: str | os.PathLike[str]) -> LogFile:
        """Open a file, replacing the current one. Raises ``OSError`` on failure."""
        file = LogFile(path)
        self._set_file(file, keep_marks=False)
        return file

    def reload(self) -> bool:
        """Re-read the current file from disk; marks survive if it is the same file."""
        if self.file is None:
            return False
        try:
            file = LogFile(self.file.path)
        except (OSError, ValueError) as exc:
            self._set_message(f"Could not reload the file: {exc}")
            return False
        self._set_file(file, keep_marks=self.file.fingerprint.matches(file.path))
        return True

    def close(self) -> None:
        self._cancel_jobs()
        self.file = None
        self.marks.clear()
        self.message = ""
        self._rows = AllRows(lambda: self.line_count)
        self._notify(Change.ALL)

    def poll(self) -> FileChange | None:
        """Look for changes on disk: new data is indexed, a replaced file reloaded."""
        if self.file is None or self._index_job is not None:
            return None
        change = self.file.check()
        if change is FileChange.MISSING:
            if not self._missing:
                self._missing = True
                self._set_message("The file is gone. Waiting for it to reappear…")
            return change
        if self._missing:
            self._missing = False
            self._set_message("")
        if change is FileChange.GROWN:
            self._start_indexing()
        elif change is FileChange.REPLACED and self.reload():
            self._set_message("The file was truncated or replaced, so it was reloaded.")
        return change

    def _set_file(self, file: LogFile, *, keep_marks: bool) -> None:
        self._cancel_jobs()
        self.file = file
        self.message = ""
        self._missing = False
        if not keep_marks:
            self.marks.clear()
        self._start_indexing()
        self._restart_scans()
        self._notify(Change.ALL)

    def _start_indexing(self) -> None:
        file = self.file
        if file is None or self._index_job is not None:
            return
        start, end = file.indexed_size, file.disk_size
        if end <= start:
            return
        self._index_range = (start, end)

        def work() -> Generator[IndexBatch | None]:
            return index_lines(file.path, start, end)

        def on_progress(batch: IndexBatch) -> None:
            file.apply(batch)
            for scan in self._scans():
                scan.update()
            self._notify(Change.LINES | Change.ROWS | Change.STATUS)

        def on_done(_: None) -> None:
            self._index_job = None
            self._notify(Change.STATUS)

        def on_error(error: BaseException) -> None:
            self._index_job = None
            self._set_message(f"Could not read the file: {error}")

        self._index_job = self._executor.submit(
            work,
            priority=Priority.INDEX,
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
        )

    def read_rows(
        self, first_row: int, count: int, max_line_bytes: int | None = None
    ) -> list[tuple[int, str]]:
        """``(line number, text)`` for up to *count* visible rows from *first_row*."""
        if self.file is None or count <= 0:
            return []
        lines = self._rows.lines(first_row, first_row + count)
        result: list[tuple[int, str]] = []
        with self.file.reader() as reader:
            index = 0
            while index < len(lines):  # read runs of consecutive lines at once
                end = index + 1
                while end < len(lines) and lines[end] == lines[end - 1] + 1:
                    end += 1
                first, stop = lines[index], lines[end - 1] + 1
                texts = reader.read_lines(first, stop, max_line_bytes)
                result.extend(zip(range(first, stop), texts, strict=True))
                index = end
        return result

    # -- search ---------------------------------------------------------------

    @property
    def search_query(self) -> Query | None:
        return self._search_query

    @property
    def search_pattern(self) -> re.Pattern[str] | None:
        """The search compiled for highlighting single lines."""
        return self._search_pattern

    @property
    def search_scan(self) -> LineScan | None:
        return self._search_scan

    def set_search(self, query: Query | None) -> None:
        """Search for *query*; ``None`` or empty text clears. Raises :class:`QueryError`."""
        if query is not None and not query.text:
            query = None
        if query == self._search_query:
            return
        pattern = query.compile() if query else None
        self._search_query, self._search_pattern = query, pattern
        self._search_matcher = query.matcher() if query else None
        self._restart_search()
        changes = Change.SEARCH | Change.STATUS
        if self._filter_to_search:
            self._restart_filter()
            changes |= Change.ROWS
        self._notify(changes)

    # -- filters --------------------------------------------------------------

    @property
    def rows(self) -> RowMap:
        """The rows the view shows (all lines, or those passing the filters)."""
        return self._rows

    @property
    def filter_scan(self) -> LineScan | None:
        return self._filter_scan

    @property
    def filter_to_search(self) -> bool:
        return self._filter_to_search

    def set_filter_to_search(self, enabled: bool) -> None:
        """Show only the lines matching the search (like ``grep``)."""
        if enabled != self._filter_to_search:
            self._filter_to_search = enabled
            self._restart_filter()
            self._notify(Change.ROWS | Change.STATUS)

    @property
    def hide_empty(self) -> bool:
        return self._hide_empty

    def set_hide_empty(self, enabled: bool) -> None:
        if enabled != self._hide_empty:
            self._hide_empty = enabled
            self._compile_exclude()
            self._restart_filter()
            self._notify(Change.ROWS | Change.STATUS)

    # -- rules ----------------------------------------------------------------

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    @property
    def highlighters(self) -> list[Highlighter]:
        return self._highlighters

    def rule_error(self, index: int) -> str | None:
        return self._rule_errors.get(index)

    def rule_scan(self, rule: Rule) -> LineScan | None:
        return self._rule_scans.get(rule.query) if rule.highlights else None

    def set_rules(self, rules: Iterable[Rule]) -> None:
        self._rules = list(rules)
        old_hide = self._hide_queries
        self._compile_rules()
        self._sync_rule_scans()
        changes = Change.RULES | Change.STATUS
        if self._hide_queries != old_hide:
            self._restart_filter()
            changes |= Change.ROWS
        self._notify(changes)

    def _compile_rules(self) -> None:
        self._rule_errors = {}
        self._highlighters = []
        hide: list[Query] = []
        for index, rule in enumerate(self._rules):
            if not rule.enabled or not rule.query.text:
                continue
            try:
                pattern = rule.query.compile()
            except QueryError as exc:
                self._rule_errors[index] = str(exc)
                continue
            if rule.action is RuleAction.HIDE:
                hide.append(rule.query)
            else:
                self._highlighters.append(Highlighter(index, rule, pattern))
        self._hide_queries = tuple(hide)
        self._compile_exclude()

    def _compile_exclude(self) -> None:
        extra = (EMPTY_LINE_SOURCE,) if self._hide_empty else ()
        try:
            self._exclude = combine(self._hide_queries, *extra)
        except QueryError as exc:  # e.g. two regexes defining the same group name
            self._exclude = combine((), *extra)
            self.message = f"Hide rules ignored: {exc}"

    # -- marks ----------------------------------------------------------------

    def toggle_mark(self, line: int) -> Mark | None:
        mark = self.marks.toggle(line)
        self._notify(Change.MARKS)
        return mark

    def add_mark(
        self, first: int, last: int, color: str = DEFAULT_MARK_COLOR, label: str = ""
    ) -> Mark:
        mark = self.marks.add(first, last, color, label)
        self._notify(Change.MARKS)
        return mark

    def edit_mark(
        self,
        mark: Mark,
        *,
        first: int | None = None,
        last: int | None = None,
        color: str | None = None,
        label: str | None = None,
    ) -> None:
        if color is not None:
            mark.color = color
        if label is not None:
            mark.label = label
        self.marks.update(mark, first=first, last=last)
        self._notify(Change.MARKS)

    def remove_mark(self, mark: Mark) -> None:
        self.marks.remove(mark)
        self._notify(Change.MARKS)

    def set_marks(self, marks: Iterable[Mark]) -> None:
        self.marks.replace_all(marks)
        self._notify(Change.MARKS)

    # -- navigation -----------------------------------------------------------

    def is_visible(self, line: int) -> bool:
        return self._rows.row_of(line) is not None

    def find(self, lines: LineSet, current: int, *, backwards: bool = False) -> Hit | None:
        """Next/previous visible line of *lines* from *current*, wrapping around."""
        return step(lines, current, backwards=backwards, visible=self.is_visible)

    # -- activity -------------------------------------------------------------

    def activities(self) -> list[Activity]:
        """Running background work, most important first."""
        found: list[Activity] = []
        if self._index_job is not None and self.file is not None:
            start, end = self._index_range
            done = (self.file.indexed_size - start) / max(1, end - start)
            found.append(Activity("Loading", min(1.0, done)))
        if self._filter_scan is not None and self._filter_scan.running:
            found.append(Activity("Filtering", self._filter_scan.progress))
        if self._search_scan is not None and self._search_scan.running:
            found.append(Activity("Searching", self._search_scan.progress))
        running = [scan for scan in self._rule_scans.values() if scan.running]
        if running:
            found.append(Activity("Analysing", min(scan.progress for scan in running)))
        return found

    def _set_message(self, message: str) -> None:
        self.message = message
        self._notify(Change.STATUS)

    # -- scans ----------------------------------------------------------------

    def _scans(self) -> list[LineScan]:
        scans = [self._filter_scan, self._search_scan, *self._rule_scans.values()]
        return [scan for scan in scans if scan is not None]

    def _cancel_jobs(self) -> None:
        if self._index_job is not None:
            self._index_job.cancel()
            self._index_job = None
        for scan in self._scans():
            scan.cancel()
        self._filter_scan = self._search_scan = None
        self._rule_scans = {}

    def _new_scan(
        self, make_scanner: Callable[[], ChunkScanner], priority: Priority, change: Change
    ) -> LineScan | None:
        if self.file is None:
            return None
        scan = LineScan(
            self.file,
            self._executor,
            make_scanner,
            priority,
            lambda: self._notify(change | Change.STATUS),
        )
        scan.update()
        return scan

    def _restart_scans(self) -> None:
        self._restart_search()
        self._rule_scans = {}
        self._sync_rule_scans()
        self._restart_filter()

    def _restart_search(self) -> None:
        if self._search_scan is not None:
            self._search_scan.cancel()
        matcher = self._search_matcher
        self._search_scan = (
            self._new_scan(lambda: MatchScanner(matcher), Priority.SEARCH, Change.SEARCH)
            if matcher
            else None
        )

    def _sync_rule_scans(self) -> None:
        wanted = {h.rule.query for h in self._highlighters}
        for query in [q for q in self._rule_scans if q not in wanted]:
            self._rule_scans.pop(query).cancel()
        for query in wanted:
            if query not in self._rule_scans:
                scan = self._new_scan(
                    partial(MatchScanner, query.matcher()), Priority.ANALYSIS, Change.RULES
                )
                if scan is not None:
                    self._rule_scans[query] = scan

    def _restart_filter(self) -> None:
        if self._filter_scan is not None:
            self._filter_scan.cancel()
            self._filter_scan = None
        include = self._search_matcher if self._filter_to_search else None
        exclude = self._exclude
        if include is not None or exclude is not None:
            self._filter_scan = self._new_scan(
                lambda: VisibleScanner(include, exclude), Priority.SEARCH, Change.ROWS
            )
        if self._filter_scan is not None:
            self._rows = FilteredRows(self._filter_scan.lines)
        else:
            self._rows = AllRows(lambda: self.line_count)
