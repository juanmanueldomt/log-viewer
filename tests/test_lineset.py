from __future__ import annotations

from jmlogviewer.core.lineset import AllRows, FilteredRows, LineSet


class TestLineSet:
    def test_basics(self) -> None:
        lines = LineSet([2, 5, 9])
        assert len(lines) == 3
        assert list(lines) == [2, 5, 9]
        assert 5 in lines
        assert 6 not in lines
        assert "5" not in lines
        assert lines.rank(5) == 1
        assert lines.slice(1, 3) == [5, 9]

    def test_navigation(self) -> None:
        lines = LineSet([2, 5, 9])
        assert lines.next_after(2) == 5
        assert lines.next_after(9) is None
        assert lines.prev_before(5) == 2
        assert lines.prev_before(2) is None

    def test_extend_and_truncate(self) -> None:
        lines = LineSet([1, 2])
        lines.extend([4, 8])
        lines.truncate(4)
        assert list(lines) == [1, 2]

    def test_any_between(self) -> None:
        lines = LineSet([10, 20])
        assert lines.any_between(5, 10)
        assert lines.any_between(11, 25)
        assert not lines.any_between(11, 19)
        assert not lines.any_between(21, 30)


class TestRowMaps:
    def test_all_rows_follow_the_count(self) -> None:
        count = 3
        rows = AllRows(lambda: count)
        assert not rows.filtered
        assert len(rows) == 3
        count = 5
        assert len(rows) == 5
        assert rows.lines(3, 10) == [3, 4]
        assert rows.row_of(4) == 4
        assert rows.row_of(5) is None
        assert rows.nearest_row(99) == 4

    def test_filtered_rows(self) -> None:
        rows = FilteredRows(LineSet([3, 7, 8]))
        assert rows.filtered
        assert len(rows) == 3
        assert rows.line_at(1) == 7
        assert rows.lines(0, 2) == [3, 7]
        assert rows.row_of(7) == 1
        assert rows.row_of(4) is None
        assert rows.nearest_row(4) == 1
        assert rows.nearest_row(100) == 2
        assert rows.nearest_row(0) == 0

    def test_empty_filtered_rows(self) -> None:
        rows = FilteredRows(LineSet())
        assert len(rows) == 0
        assert rows.nearest_row(5) == 0
        assert rows.row_of(5) is None
