from __future__ import annotations

from datetime import datetime

from jmlogviewer.core import export
from jmlogviewer.core.logfile import LogFile
from jmlogviewer.core.marks import Mark

from .conftest import MakeFile, index_fully


def test_marks_report(make_file: MakeFile) -> None:
    file = index_fully(LogFile(make_file("".join(f"line {i}\n" for i in range(1, 13)))))
    report = export.marks_report(
        file, [Mark(1, 2, label="Startup"), Mark(10, 10)], now=datetime(2026, 1, 2, 3, 4)
    )
    assert "# Marks in test.log" in report
    assert "- Exported: 2026-01-02 03:04" in report
    assert "## 1. Startup" in report
    assert "Lines 2\N{EN DASH}3" in report
    assert " 2  line 2\n 3  line 3" in report
    assert "## 2. Mark 2" in report
    assert "Line 11\n" in report
    assert "11  line 11" in report


def test_long_marks_are_capped(make_file: MakeFile, monkeypatch: object) -> None:
    file = index_fully(LogFile(make_file("x\n" * 30)))
    monkeypatch.setattr(export, "MAX_EXPORT_LINES_PER_MARK", 5)  # type: ignore[attr-defined]
    report = export.marks_report(file, [Mark(0, 29)])
    assert report.count("  x") == 5
    assert "25 more lines" in report
