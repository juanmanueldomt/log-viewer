"""Export marked sections of a log as a Markdown report to share findings."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .logfile import LogFile
from .marks import Mark

MAX_EXPORT_LINES_PER_MARK = 10_000


def marks_report(file: LogFile, marks: Iterable[Mark], now: datetime | None = None) -> str:
    """Markdown with each mark's label, line range and the marked lines."""
    now = now or datetime.now()
    out = [
        f"# Marks in {file.name}",
        "",
        f"- File: `{file.path}`",
        f"- Exported: {now:%Y-%m-%d %H:%M}",
        "",
    ]
    width = len(str(file.line_count))
    for number, mark in enumerate(marks, start=1):
        title = mark.label or f"Mark {number}"
        plural = "s" if mark.size > 1 else ""
        out += [f"## {number}. {title}", "", f"Line{plural} {mark.describe_lines()}", "", "```"]
        last = min(mark.last, mark.first + MAX_EXPORT_LINES_PER_MARK - 1)
        for offset, text in enumerate(file.read_lines(mark.first, last + 1)):
            out.append(f"{mark.first + offset + 1:>{width}}  {text}")
        if last < mark.last:
            out.append(f"{'':>{width}}  \N{HORIZONTAL ELLIPSIS} {mark.last - last:,} more lines")
        out += ["```", ""]
    return "\n".join(out)
