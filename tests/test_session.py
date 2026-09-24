from __future__ import annotations

from array import array
from pathlib import Path

import pytest

from jmlogviewer.core import session as session_module
from jmlogviewer.core.lineset import LineSet
from jmlogviewer.core.logfile import FileChange, index_lines
from jmlogviewer.core.query import Query, QueryError
from jmlogviewer.core.rules import Rule, RuleAction
from jmlogviewer.core.session import Change, Hit, LineScan, LogSession, step
from jmlogviewer.core.tasks import ManualExecutor, Priority

from .conftest import MakeFile


def texts(session: LogSession) -> list[str]:
    return [text for _, text in session.read_rows(0, len(session.rows))]


def search_lines(session: LogSession) -> list[int]:
    scan = session.search_scan
    assert scan is not None
    return list(scan.lines)


def append(path: Path, data: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(data)


class TestOpening:
    def test_open_and_read(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        session.open(make_file("a\nb\nc\n"))
        assert session.loading
        executor.run_pending()
        assert not session.loading
        assert session.line_count == 3
        assert session.read_rows(1, 5) == [(1, "b"), (2, "c")]

    def test_open_missing_file_raises(self, session: LogSession, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            session.open(tmp_path / "nope.log")
        assert session.file is None

    def test_listeners(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        changes: list[Change] = []
        unsubscribe = session.subscribe(changes.append)
        session.open(make_file("a\n"))
        assert changes == [Change.ALL]
        executor.run_pending()
        assert any(Change.LINES in change for change in changes)
        unsubscribe()
        changes.clear()
        session.close()
        assert changes == []
        assert session.file is None
        assert session.line_count == 0

    def test_activities_while_loading(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        session.open(make_file("x\n" * 10))
        assert [activity.label for activity in session.activities()] == ["Loading"]
        executor.run_pending()
        assert session.activities() == []

    def test_progressive_loading_keeps_derived_data_consistent(
        self,
        session: LogSession,
        executor: ManualExecutor,
        make_file: MakeFile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            session_module,
            "index_lines",
            lambda path, start, end: index_lines(path, start, end, chunk_bytes=7, interval=0),
        )
        content = "".join(f"{'ERROR' if i % 3 == 0 else 'INFO'} line {i}\n" for i in range(200))
        session.open(make_file(content))
        session.set_search(Query("ERROR"))
        session.set_filter_to_search(True)
        session.set_rules([Rule(Query("INFO"))])
        executor.run_pending()
        assert search_lines(session) == list(range(0, 200, 3))
        assert len(session.rows) == 67
        info = session.rule_scan(session.rules[0])
        assert info is not None
        assert len(info.lines) == 133


class TestSearch:
    def test_hits_and_navigation(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        session.open(make_file("INFO a\nERROR b\nINFO c\nerror d\n"))
        session.set_search(Query("error"))
        executor.run_pending()
        scan = session.search_scan
        assert scan is not None
        assert scan.complete
        assert list(scan.lines) == [1, 3]
        assert session.find(scan.lines, 0) == Hit(1, wrapped=False)
        assert session.find(scan.lines, 3) == Hit(1, wrapped=True)
        assert session.find(scan.lines, 1, backwards=True) == Hit(3, wrapped=True)

    def test_invalid_query_keeps_the_previous_search(
        self, session: LogSession, make_file: MakeFile
    ) -> None:
        session.open(make_file("x\n"))
        session.set_search(Query("x"))
        with pytest.raises(QueryError):
            session.set_search(Query("(", regex=True))
        assert session.search_query == Query("x")

    def test_empty_query_clears(self, session: LogSession, make_file: MakeFile) -> None:
        session.open(make_file("x\n"))
        session.set_search(Query("x"))
        session.set_search(Query(""))
        assert session.search_query is None
        assert session.search_pattern is None
        assert session.search_scan is None

    def test_superseded_results_are_dropped(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        session.open(make_file("a\nb\n"))
        session.set_search(Query("a"))
        session.set_search(Query("b"))
        executor.run_pending()
        assert search_lines(session) == [1]

    def test_search_before_opening_applies_to_the_file(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        session.set_search(Query("b"))
        session.open(make_file("a\nb\n"))
        executor.run_pending()
        assert search_lines(session) == [1]


class TestFilters:
    def test_filter_to_search(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        session.open(make_file("a1\nb\na2\nb\n"))
        session.set_search(Query("a"))
        session.set_filter_to_search(True)
        executor.run_pending()
        assert session.rows.filtered
        assert texts(session) == ["a1", "a2"]
        session.set_filter_to_search(False)
        assert not session.rows.filtered
        assert len(session.rows) == 4

    def test_filter_with_empty_search_shows_everything(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        session.open(make_file("a\nb\n"))
        session.set_filter_to_search(True)
        executor.run_pending()
        assert not session.rows.filtered
        assert len(session.rows) == 2

    def test_hide_rules_and_empty_lines(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        session.open(make_file("keep\nnoise 1\n\n  \nkeep 2\n"))
        session.set_rules([Rule(Query("noise"), action=RuleAction.HIDE)])
        executor.run_pending()
        assert texts(session) == ["keep", "", "  ", "keep 2"]
        session.set_hide_empty(True)
        executor.run_pending()
        assert texts(session) == ["keep", "keep 2"]
        assert session.find(LineSet([1, 4]), 0) == Hit(4, wrapped=False)

    def test_read_rows_of_a_filtered_view(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        session.open(make_file("".join(f"a{i}\n" for i in range(10))))
        session.set_rules([Rule(Query("a[37]", regex=True), action=RuleAction.HIDE)])
        executor.run_pending()
        assert session.read_rows(1, 5) == [(1, "a1"), (2, "a2"), (4, "a4"), (5, "a5"), (6, "a6")]
        assert session.rows.nearest_row(3) == 3


class TestRules:
    def test_highlighters_and_hits(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        rules = [
            Rule(Query("ERROR")),
            Rule(Query("WARN"), action=RuleAction.MATCH),
            Rule(Query("x"), enabled=False),
            Rule(Query("ok"), action=RuleAction.HIDE),
        ]
        session.set_rules(rules)
        session.open(make_file("ERROR\nWARN\nok\nERROR\n"))
        executor.run_pending()
        assert [h.index for h in session.highlighters] == [0, 1]
        error_scan = session.rule_scan(rules[0])
        assert error_scan is not None
        assert list(error_scan.lines) == [0, 3]
        assert session.rule_scan(rules[2]) is None
        assert session.rule_scan(rules[3]) is None
        assert texts(session) == ["ERROR", "WARN", "ERROR"]

    def test_recolouring_keeps_the_scan(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        rule = Rule(Query("ERROR"))
        session.open(make_file("ERROR\n"))
        session.set_rules([rule])
        executor.run_pending()
        scan = session.rule_scan(rule)
        recoloured = rule.with_changes(color="#000000")
        session.set_rules([recoloured])
        assert session.rule_scan(recoloured) is scan
        assert executor.pending == 0

    def test_invalid_rule_is_reported(self, session: LogSession) -> None:
        session.set_rules([Rule(Query("(", regex=True))])
        assert session.rule_error(0)
        assert session.highlighters == []


class TestTail:
    def test_growth_rescans_the_partial_last_line(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        path = make_file("INFO start\nERR")
        session.open(path)
        session.set_search(Query("ERROR"))
        session.set_filter_to_search(True)
        executor.run_pending()
        assert session.line_count == 2
        assert search_lines(session) == []
        assert session.poll() is FileChange.UNCHANGED

        append(path, b"OR boom\nINFO more\nERROR again\n")
        assert session.poll() is FileChange.GROWN
        executor.run_pending()
        assert session.line_count == 4
        assert search_lines(session) == [1, 3]
        assert texts(session) == ["ERROR boom", "ERROR again"]

    def test_partial_line_that_stops_matching_is_dropped(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        path = make_file("a\nERROR")
        session.open(path)
        session.set_search(Query("ERROR", whole_word=True))
        executor.run_pending()
        assert search_lines(session) == [1]
        append(path, b"S\n")
        session.poll()
        executor.run_pending()
        assert search_lines(session) == []

    def test_truncation_reloads_and_drops_marks(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        path = make_file("old 1\nold 2\n")
        session.open(path)
        executor.run_pending()
        session.add_mark(0, 1)
        path.write_bytes(b"new\n")
        assert session.poll() is FileChange.REPLACED
        executor.run_pending()
        assert session.line_count == 1
        assert "reloaded" in session.message
        assert not session.marks

    def test_reload_keeps_marks_of_the_same_file(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        path = make_file("a\nb\n")
        session.open(path)
        executor.run_pending()
        session.add_mark(1, 1)
        append(path, b"c\n")
        assert session.reload()
        executor.run_pending()
        assert len(session.marks) == 1
        assert session.line_count == 3

    def test_missing_file_then_recreated(
        self, session: LogSession, executor: ManualExecutor, make_file: MakeFile
    ) -> None:
        path = make_file("a\nb\n")
        session.open(path)
        executor.run_pending()
        path.unlink()
        assert session.poll() is FileChange.MISSING
        assert "gone" in session.message
        path.write_bytes(b"rotated\n")
        assert session.poll() is FileChange.REPLACED
        executor.run_pending()
        assert texts(session) == ["rotated"]


class TestMarks:
    def test_mark_operations_notify(self, session: LogSession) -> None:
        changes: list[Change] = []
        session.subscribe(changes.append)
        mark = session.add_mark(5, 2)
        assert (mark.first, mark.last) == (2, 5)
        session.edit_mark(mark, label="boot", color="#000000")
        assert (mark.label, mark.color) == ("boot", "#000000")
        assert session.toggle_mark(3) is None
        assert changes == [Change.MARKS] * 3
        assert not session.marks


def test_step() -> None:
    lines = LineSet([1, 5, 9])
    assert step(lines, 1, backwards=False, visible=lambda line: line != 5) == Hit(9, False)
    assert step(lines, 9, backwards=False, visible=lambda line: False) is None
    assert step(lines, 0, backwards=True, visible=lambda line: True) == Hit(9, True)
    assert step(LineSet(), 0, backwards=False, visible=lambda line: True) is None
    assert step(LineSet([4]), 4, backwards=False, visible=lambda line: True) == Hit(4, True)


def test_line_scan_errors_are_reported(
    executor: ManualExecutor, make_file: MakeFile, session: LogSession
) -> None:
    file = session.open(make_file("x\n"))
    executor.run_pending()

    def broken(text: str, first: int, stop: int) -> array[int]:
        raise RuntimeError("boom")

    scan = LineScan(file, executor, lambda: broken, Priority.SEARCH, lambda: None)
    scan.update()
    executor.run_pending()
    assert scan.error == "boom"
    assert not scan.running
