"""The log view: displays files of any size by rendering only the rows on screen.

A ``tk.Text`` holds just the visible rows and is re-filled when scrolling, so
memory and speed do not depend on the file size. The Text class bindings are
removed; scrolling, selection (in file coordinates, so it can span any number
of rows) and keyboard navigation are implemented here.
"""

from __future__ import annotations

import contextlib
import math
import re
import sys
import tkinter as tk
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from tkinter import ttk
from typing import NamedTuple

from ..core.lineset import LineSet
from ..core.marks import Mark
from ..core.rules import RuleAction
from ..core.session import LogSession
from .theme import Fonts, Palette, line_tint, mark_tint, match_tint, readable_on

MAX_DISPLAY_CHARS = 4000
"""Longer lines are cut on screen; copying still gets the whole line."""
MAX_LINE_BYTES = MAX_DISPLAY_CHARS * 4
MAX_MATCHES_PER_LINE = 100
MAX_COPY_ROWS = 250_000
WHEEL_ROWS = 3
END = 1_000_000_000
"""Column meaning "end of line"."""

PAD_X, PAD_Y = 8, 4
MARK_ZONE = 12  # left part of the gutter where marks are drawn and toggled
RULER_WIDTH = 14
SHIFT, CONTROL = 0x1, 0x4
IS_MAC = sys.platform == "darwin"

_WORD = re.compile(r"\w[\w.\-]*\w|\w")
# Tk 8.6 counts characters outside the BMP as two, which would shift tag
# positions, and some X11 builds crash drawing color emoji: show a placeholder.
_ASTRAL = re.compile(f"[{chr(0x10000)}-{chr(0x10FFFF)}]") if tk.TkVersion < 9.0 else None


class Position(NamedTuple):
    """A place in the view: a row and a character column (ordered like text)."""

    row: int
    col: int


@dataclass(frozen=True, slots=True)
class ContextInfo:
    """What the context menu acts on."""

    line: int
    text: str  # single-line selection, else the word under the pointer
    lines: tuple[int, int] | None  # first and last line of a multi-row selection
    mark: Mark | None


class LogView(ttk.Frame):
    """Read-only, virtualized view of the rows of a :class:`LogSession`."""

    def __init__(
        self, master: tk.Misc, session: LogSession, palette: Palette, fonts: Fonts
    ) -> None:
        super().__init__(master, style="View.TFrame")
        self.session = session
        self.on_view_changed: Callable[[], None] = lambda: None
        self.on_message: Callable[[str], None] = lambda message: None
        self.on_toggle_mark: Callable[[int, bool], None] = lambda line, extend: None
        self.on_context_menu: Callable[[tk.Event[tk.Misc], ContextInfo], None] = (
            lambda event, info: None
        )
        self.on_zoom: Callable[[int], None] = lambda step: None

        self._palette = palette
        self._fonts = fonts
        # Scrolling: the first row shown, or anchored to the end of the file.
        self._top = 0
        self._at_end = False
        self._end_visible = True
        self._full_rows = 1
        self._pending_anchor: tuple[int, int] | None = None
        # Current line (file line number) and selection (rows).
        self._current: int | None = None
        self._anchor: Position | None = None
        self._head: Position | None = None
        self._line_anchor: int | None = None
        # Rendering.
        self._rendered: list[tuple[int, str]] = []
        self._first_row = 0
        self._render_id: str | None = None
        self._ruler_dirty = True
        self._rule_signature: tuple[object, ...] | None = None
        self._mark_tags: set[str] = set()
        self._gutter_width = 0
        self._height = 1
        self._line_px = 1
        # Pointer interaction.
        self._wheel = 0.0
        self._dragging = False
        self._pointer = (0, 0)
        self._autoscroll_id: str | None = None
        self._thumb_grab: float | None = None
        self._thumb_hover = False
        self._hover_row: int | None = None

        self._gutter = tk.Canvas(self, highlightthickness=0, borderwidth=0, takefocus=False)
        self._text = tk.Text(
            self,
            wrap="none",
            undo=False,
            borderwidth=0,
            highlightthickness=0,
            padx=PAD_X,
            pady=PAD_Y,
            spacing1=1,
            spacing3=1,
            cursor="xterm",
            exportselection=False,
            insertwidth=0,
            width=1,
            height=1,
            takefocus=True,
        )
        # No "Text" class bindings: the widget must not edit, select or scroll itself.
        self._text.bindtags((str(self._text), str(self.winfo_toplevel()), "all"))
        self._ruler = tk.Canvas(
            self, width=RULER_WIDTH, highlightthickness=0, borderwidth=0, takefocus=False
        )
        self._hbar = ttk.Scrollbar(
            self, orient="horizontal", style="Slim.Horizontal.TScrollbar", command=self._text.xview
        )
        self._text.configure(xscrollcommand=self._hbar.set)

        self._gutter.grid(row=0, column=0, sticky="ns")
        self._text.grid(row=0, column=1, sticky="nsew")
        self._ruler.grid(row=0, column=2, rowspan=2, sticky="ns")
        self._hbar.grid(row=1, column=1, sticky="ew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.apply_palette(palette)
        self.font_changed()
        self._bind_events()

    # -- public API -----------------------------------------------------------

    @property
    def current_line(self) -> int | None:
        return self._current

    @property
    def top_row(self) -> int:
        return self._top

    @property
    def page_rows(self) -> int:
        return self._full_rows

    @property
    def at_end(self) -> bool:
        """Whether the view sticks to the end of the file (and so follows growth)."""
        return self._at_end or (self._end_visible and self._top == 0)

    def focus(self) -> None:
        self._text.focus_set()

    def refresh(self, *, ruler: bool = False) -> None:
        """Schedule a redraw (several requests are coalesced into one)."""
        if ruler:
            self._ruler_dirty = True
        if self._render_id is None:
            self._render_id = self.after_idle(self._render)

    def reset(self) -> None:
        """Forget position and selection, e.g. when another file is opened."""
        self._top = 0
        self._at_end = False
        self._current = None
        self._pending_anchor = None
        self.clear_selection()
        self._text.xview_moveto(0)
        self.refresh(ruler=True)

    def destroy(self) -> None:
        for after_id in (self._render_id, self._autoscroll_id):
            if after_id is not None:
                self.after_cancel(after_id)
        self._render_id = self._autoscroll_id = None
        super().destroy()

    def apply_palette(self, palette: Palette) -> None:
        self._palette = p = palette
        text = self._text
        text.configure(background=p.view, foreground=p.text)
        self._gutter.configure(background=p.view)
        self._ruler.configure(background=p.view)
        text.tag_configure("current", background=p.current_line)
        text.tag_configure("search", background=p.search, foreground=readable_on(p.search))
        text.tag_configure(
            "search_current", background=p.search_current, foreground=readable_on(p.search_current)
        )
        text.tag_configure("selection", background=p.selection, foreground=p.text)
        text.tag_configure("cut", foreground=p.faint)
        for tag in self._mark_tags:
            text.tag_delete(tag)
        self._mark_tags.clear()
        self._rule_signature = None
        self.refresh(ruler=True)

    def font_changed(self) -> None:
        font = self._fonts.mono
        self._line_px = int(font.metrics("linespace")) + 2  # plus spacing1 and spacing3
        self._text.configure(font=font, tabs=(font.measure("    "),), tabstyle="wordprocessor")
        self._gutter_width = 0
        self.refresh(ruler=True)

    def set_wrap(self, wrap: bool) -> None:
        self._text.configure(wrap="word" if wrap else "none")
        if wrap:
            self._hbar.grid_remove()
        else:
            self._hbar.grid()
        self.refresh()

    def scroll_rows(self, delta: int) -> None:
        if (delta > 0 and self._end_visible) or (delta < 0 and self._top == 0):
            return  # nothing to scroll: keep following the end if we were
        if delta < 0:
            self._at_end = False
        self._top = max(0, self._top + delta)
        self.refresh()

    def scroll_to_row(self, row: int) -> None:
        self._top = max(0, row)
        self._at_end = False
        self.refresh()

    def scroll_to_end(self) -> None:
        self._at_end = True
        self.refresh()

    def set_current_line(self, line: int | None, *, reveal: bool = True) -> None:
        """Make *line* current; with *reveal*, scroll it into view (centered if far)."""
        self._current = line
        if line is not None and reveal:
            rows = self.session.rows
            row = rows.row_of(line)
            self._reveal_row(rows.nearest_row(line) if row is None else row)
        self.refresh()

    def go_to_line(self, line: int) -> None:
        self.clear_selection()
        self.set_current_line(line)

    def keep_position(self) -> None:
        """Call before the rows change (a filter toggles) to keep the current line in place."""
        self.clear_selection()
        if self._at_end:
            return
        line = self._current
        top_index = self._top - self._first_row
        if line is None and 0 <= top_index < len(self._rendered):
            line = self._rendered[top_index][0]
        if line is None:
            return
        rendered = [number for number, _ in self._rendered]
        offset = rendered.index(line) - top_index if line in rendered else None
        self._pending_anchor = (line, self._full_rows // 3 if offset is None else offset)

    def clear_selection(self) -> None:
        if self._anchor is not None or self._head is not None:
            self._anchor = self._head = None
            self.refresh()
        self._line_anchor = None

    def select_all(self) -> None:
        total = len(self.session.rows)
        if total:
            self._anchor, self._head = Position(0, 0), Position(total - 1, END)
            self.refresh()

    def has_selection(self) -> bool:
        return self._selection() is not None

    def selected_lines(self) -> tuple[int, int] | None:
        """First and last file line of the selection, or the current line."""
        selection = self._selection()
        rows = self.session.rows
        if selection is not None:
            first, last = selection
            if last.col == 0 and last.row > first.row:  # selection ends at a line start
                last = Position(last.row - 1, END)
            return rows.line_at(first.row), rows.line_at(last.row)
        if self._current is not None:
            return self._current, self._current
        return None

    def selection_text(self) -> str | None:
        selection = self._selection()
        if selection is None:
            return None
        start, end = selection
        texts = [text for _, text in self.session.read_rows(start.row, end.row - start.row + 1)]
        if not texts:
            return None
        if len(texts) == 1:
            return texts[0][start.col : end.col]
        texts[0] = texts[0][start.col :]
        texts[-1] = texts[-1][: end.col]
        return "\n".join(texts)

    def copy(self) -> None:
        """Copy the selection, or the current line when nothing is selected."""
        selection = self._selection()
        if selection is not None:
            count = selection[1].row - selection[0].row + 1
            if count > MAX_COPY_ROWS:
                self.on_message(f"Selection too large to copy ({count:,} lines)")
                return
            text = self.selection_text() or ""
        elif self._current is not None and self.session.file is not None:
            count, text = 1, self.session.file.read_line(self._current)
        else:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.on_message(f"Copied {count:,} line{'' if count == 1 else 's'}")

    # -- rendering ------------------------------------------------------------

    def _line_height(self) -> int:
        return self._line_px

    def _render(self) -> None:
        self._render_id = None
        with contextlib.suppress(tk.TclError):  # the widget may be being destroyed
            self._render_now()

    def _render_now(self) -> None:
        total = len(self.session.rows)
        line_height = self._line_height()
        self._height = self._text.winfo_height()
        height = max(1, self._height - 2 * PAD_Y)
        capacity = max(1, math.ceil(height / line_height))
        self._full_rows = max(1, height // line_height)
        self._apply_pending_anchor()
        self._sync_rule_tags()

        if not self._at_end:
            self._top = max(0, min(self._top, total - 1))
            self._draw_rows(self._top, capacity + 1)
            if self._top > 0 and self._blank_below(total, line_height):
                self._at_end = True  # scrolled past the end: stick to it instead
        if self._at_end:
            first = max(0, total - capacity - 1)
            self._draw_rows(first, total - first)
            self._text.yview_moveto(1.0)
            self._top = self._first_visible_row()
        self._end_visible = total == 0 or self._row_fully_visible(total - 1)
        self._draw_gutter()
        self._draw_ruler()
        self.on_view_changed()

    def _draw_rows(self, first: int, count: int) -> None:
        text = self._text
        rows = self.session.read_rows(first, count, MAX_LINE_BYTES)
        if _ASTRAL is not None:
            rows = [(line, _ASTRAL.sub("�", content)) for line, content in rows]
        self._first_row = first
        self._rendered = rows
        text.delete("1.0", "end")
        if not rows:
            return
        shown = [
            content if len(content) <= MAX_DISPLAY_CHARS else content[:MAX_DISPLAY_CHARS] + " …"
            for _, content in rows
        ]
        text.insert("1.0", "\n".join(shown))
        text.yview_moveto(0.0)
        self._decorate()

    def _decorate(self) -> None:
        session = self.session
        ranges: defaultdict[str, list[str]] = defaultdict(list)  # tag -> start, end, start, ...
        rendered = self._rendered
        highlighters = session.highlighters
        search = session.search_pattern
        marks = session.marks.overlapping(rendered[0][0], rendered[-1][0])
        selection = self._selection()
        for i, (line, content) in enumerate(rendered):
            n = i + 1
            start, end = f"{n}.0", f"{n + 1}.0"  # including the newline: full-width background
            visible = content[:MAX_DISPLAY_CHARS]
            if line == self._current:
                ranges["current"] += (start, end)
            if marks:
                mark = _innermost(marks, line)
                if mark is not None:
                    ranges[self._mark_tag(mark.color)] += (start, end)
            line_tinted = False
            for highlighter in highlighters:
                tag = f"rule{highlighter.index}"
                if highlighter.rule.action is RuleAction.LINE:
                    if not line_tinted and highlighter.pattern.search(visible):
                        ranges[tag] += (start, end)
                        line_tinted = True
                else:
                    _add_matches(ranges[tag], n, highlighter.pattern.finditer(visible))
            if search is not None:
                tag = "search_current" if line == self._current else "search"
                _add_matches(ranges[tag], n, search.finditer(visible))
            if selection is not None:
                row = self._first_row + i
                (first_row, first_col), (last_row, last_col) = selection
                if first_row <= row <= last_row:
                    col = first_col if row == first_row else 0
                    ranges["selection"] += (
                        f"{n}.{col}",
                        f"{n}.{last_col}" if row == last_row else end,
                    )
            if len(content) > MAX_DISPLAY_CHARS:
                ranges["cut"] += (f"{n}.{MAX_DISPLAY_CHARS}", f"{n}.end")
        for tag, indices in ranges.items():  # one Tk call per tag: calls are the costly part
            if indices:
                self._text.tag_add(tag, *indices)

    def _sync_rule_tags(self) -> None:
        highlighters = self.session.highlighters
        signature = tuple((h.index, h.rule.color, h.rule.action) for h in highlighters)
        if signature == self._rule_signature:
            return
        text, palette = self._text, self._palette
        for tag in text.tag_names():
            if tag.startswith("rule"):
                text.tag_delete(tag)
        for highlighter in highlighters:
            color = highlighter.rule.color
            if highlighter.rule.action is RuleAction.LINE:
                text.tag_configure(f"rule{highlighter.index}", background=line_tint(color, palette))
            else:
                background = match_tint(color, palette)
                text.tag_configure(
                    f"rule{highlighter.index}",
                    background=background,
                    foreground=readable_on(background),
                )
        self._rule_signature = signature
        self._order_tags()

    def _mark_tag(self, color: str) -> str:
        tag = f"mark{color.lstrip('#')}"
        if tag not in self._mark_tags:
            self._text.tag_configure(tag, background=mark_tint(color, self._palette))
            self._mark_tags.add(tag)
            self._order_tags()
        return tag

    def _order_tags(self) -> None:
        """Stack tags: later ones win (rules listed first have priority)."""
        highlighters = self.session.highlighters
        line_rules = [h for h in highlighters if h.rule.action is RuleAction.LINE]
        match_rules = [h for h in highlighters if h.rule.action is not RuleAction.LINE]
        order = [
            "current",
            *sorted(self._mark_tags),
            *(f"rule{h.index}" for h in reversed(line_rules)),
            *(f"rule{h.index}" for h in reversed(match_rules)),
            "search",
            "search_current",
            "selection",
            "cut",
        ]
        existing = set(self._text.tag_names())
        for tag in order:
            if tag in existing:
                self._text.tag_raise(tag)

    def _blank_below(self, total: int, line_height: int) -> bool:
        """Whether the last row is drawn with more than a line of empty space under it."""
        if not self._rendered or self._rendered[-1][0] != self.session.rows.line_at(total - 1):
            return False
        info = self._text.dlineinfo("end-1c")
        if info is None:
            return False
        return self._height - PAD_Y - (info[1] + info[3]) > line_height

    def _first_visible_row(self) -> int:
        index = self._text.index(f"@0,{PAD_Y}")
        return self._first_row + int(index.split(".")[0]) - 1

    def _row_fully_visible(self, row: int) -> bool:
        i = row - self._first_row
        if not 0 <= i < len(self._rendered):
            return False
        info = self._text.dlineinfo(f"{i + 1}.end")
        return info is not None and info[1] + info[3] <= self._height - PAD_Y + 2

    def _reveal_row(self, row: int) -> None:
        """Scroll *row* into view; after a long jump, show it a third of the way down."""
        top, page = self._top, self._full_rows
        if top <= row < top + page:
            return
        if top - page <= row < top + 2 * page:
            self.scroll_to_row(row if row < top else row - page + 1)
        else:
            self.scroll_to_row(row - page // 3)

    def _apply_pending_anchor(self) -> None:
        if self._pending_anchor is None:
            return
        line, offset = self._pending_anchor
        scan = self.session.filter_scan
        if scan is not None and not scan.complete and scan.scanned <= line:
            return  # the filter has not reached the line yet
        self._pending_anchor = None
        self._top = max(0, self.session.rows.nearest_row(line) - offset)
        self._at_end = False

    # -- gutter & overview ruler ---------------------------------------------

    def _row_boxes(self) -> list[tuple[int, int, int] | None]:
        """Top, bottom and first-line height of each rendered row (None if off screen)."""
        count = len(self._rendered)
        indices = [index for n in range(1, count + 1) for index in (f"{n}.0", f"{n}.end")]
        found = self.tk.splitlist(self.tk.call("lmap", "i", indices, f"{self._text} dlineinfo $i"))
        boxes: list[tuple[int, int, int] | None] = []
        for k in range(count):
            top = self.tk.splitlist(found[2 * k])
            if not top:
                boxes.append(None)
                continue
            end = self.tk.splitlist(found[2 * k + 1])
            bottom = int(end[1]) + int(end[3]) if end else self._height
            boxes.append((int(top[1]), bottom, int(top[3])))
        return boxes

    def _draw_gutter(self) -> None:
        gutter, palette = self._gutter, self._palette
        font = self._fonts.mono
        digits = max(3, len(str(self.session.line_count)))
        width = MARK_ZONE + font.measure("9" * digits) + 10
        if width != self._gutter_width:
            gutter.configure(width=width)
            self._gutter_width = width
        script = [f"{gutter} delete all"]
        rendered = self._rendered
        if rendered:
            marks = self.session.marks.overlapping(rendered[0][0], rendered[-1][0])
            for i, box in enumerate(self._row_boxes()):
                if box is None:
                    continue
                top, bottom, height = box
                line = rendered[i][0]
                mark = _innermost(marks, line) if marks else None
                if mark is not None:
                    bar = f"create rectangle 3 {top} 7 {bottom} -fill {mark.color} -width 0"
                    script.append(f"{gutter} {bar}")
                elif i == self._hover_row:
                    middle = top + height // 2
                    dot = f"create oval 2 {middle - 3} 8 {middle + 3} -outline {palette.faint}"
                    script.append(f"{gutter} {dot}")
                current = line == self._current
                script.append(
                    f"{gutter} create text {width - 8} {top + 1} -text {line + 1} -anchor ne"
                    f" -fill {palette.text if current else palette.faint}"
                    f" -font {self._fonts.mono_bold.name if current else font.name}"
                )
        self.tk.eval("\n".join(script))

    def _thumb_bounds(self, height: int, total: int) -> tuple[float, float] | None:
        if total <= self._full_rows:
            return None
        start = self._top / total * height
        size = max(18.0, self._full_rows / total * height)
        start = min(start, height - size)
        return start, start + size

    def _draw_ruler(self) -> None:
        ruler, palette = self._ruler, self._palette
        height = ruler.winfo_height()
        total = len(self.session.rows)
        script = []
        if self._ruler_dirty:
            self._ruler_dirty = False
            script.append(f"{ruler} delete marker")
            if total and height > 4:
                script += self._marker_commands(height, total)
        script.append(f"{ruler} delete thumb")
        bounds = self._thumb_bounds(height, total)
        if bounds is not None:
            active = self._thumb_grab is not None or self._thumb_hover
            color = palette.thumb_active if active else palette.thumb
            script.append(
                f"{ruler} create rectangle 0 {bounds[0]:.1f} {RULER_WIDTH} {bounds[1]:.1f}"
                f" -fill {color} -width 0 -tags thumb"
            )
            script.append(f"{ruler} lower thumb")
        self.tk.eval("\n".join(script))

    def _marker_commands(self, height: int, total: int) -> list[str]:
        """Draw where rule hits, marks and search hits are in the whole file."""
        session, rows = self.session, self.session.rows
        buckets = max(1, height // 3)
        size = height / buckets

        def hit_buckets(lines: LineSet) -> set[int]:
            found: set[int] = set()
            if len(lines) <= buckets * 8:
                for line in lines:
                    row = rows.row_of(line)
                    if row is not None:
                        found.add(row * buckets // total)
                return found
            for bucket in range(buckets):
                first = bucket * total // buckets
                last = max(first, (bucket + 1) * total // buckets - 1)
                if first < total and lines.any_between(
                    rows.line_at(first), rows.line_at(min(last, total - 1))
                ):
                    found.add(bucket)
            return found

        script: list[str] = []

        def draw(lane: tuple[int, int], colored: dict[int, str]) -> None:
            """Draw consecutive buckets of the same color as one bar."""
            runs: list[tuple[int, int, str]] = []
            for bucket in sorted(colored):
                color = colored[bucket]
                if runs and runs[-1][1] == bucket - 1 and runs[-1][2] == color:
                    runs[-1] = (runs[-1][0], bucket, color)
                else:
                    runs.append((bucket, bucket, color))
            for first, last, color in runs:
                script.append(
                    f"{self._ruler} create rectangle {lane[0]} {first * size:.1f} {lane[1]}"
                    f" {(last + 1) * size + 1:.1f} -fill {color} -width 0 -tags marker"
                )

        rule_colors: dict[int, str] = {}
        for highlighter in reversed(session.highlighters):  # first rule wins
            scan = session.rule_scan(highlighter.rule)
            if scan is not None:
                for bucket in hit_buckets(scan.lines):
                    rule_colors[bucket] = highlighter.rule.color
        draw((1, 5), rule_colors)

        mark_colors: dict[int, str] = {}
        for mark in session.marks:
            first = rows.nearest_row(mark.first) * buckets // total
            last = rows.nearest_row(mark.last) * buckets // total
            for bucket in range(first, last + 1):
                mark_colors[bucket] = mark.color
        draw((5, 9), mark_colors)

        scan = session.search_scan
        if scan is not None:
            draw((9, 13), dict.fromkeys(hit_buckets(scan.lines), self._palette.search_current))
        return script

    # -- events ---------------------------------------------------------------

    def _bind_events(self) -> None:
        text, gutter, ruler = self._text, self._gutter, self._ruler
        text.bind("<Configure>", lambda _e: self.refresh(ruler=True))
        ruler.bind("<Configure>", lambda _e: self.refresh(ruler=True))
        text.bind("<Button-1>", self._on_press)
        text.bind("<Shift-Button-1>", self._on_shift_press)
        text.bind("<B1-Motion>", self._on_drag)
        text.bind("<ButtonRelease-1>", self._on_release)
        text.bind("<Double-Button-1>", self._on_double)
        text.bind("<Triple-Button-1>", self._on_triple)
        for sequence in ("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",):
            text.bind(sequence, self._on_context)
        for widget in (text, gutter, ruler):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, self._on_wheel)

        keys: dict[str, Callable[[], object]] = {
            "<Up>": lambda: self._move(-1),
            "<Down>": lambda: self._move(1),
            "<Shift-Up>": lambda: self._move(-1, extend=True),
            "<Shift-Down>": lambda: self._move(1, extend=True),
            "<Prior>": lambda: self._move(-max(1, self._full_rows - 1)),
            "<Next>": lambda: self._move(max(1, self._full_rows - 1)),
            "<Shift-Prior>": lambda: self._move(-max(1, self._full_rows - 1), extend=True),
            "<Shift-Next>": lambda: self._move(max(1, self._full_rows - 1), extend=True),
            "<Home>": self._home,
            "<Control-Home>": self._home,
            "<End>": self._end,
            "<Control-End>": self._end,
            "<Left>": lambda: self._scroll_x(-4),
            "<Right>": lambda: self._scroll_x(4),
            "<Escape>": self.clear_selection,
            "<<Copy>>": self.copy,
            "<<SelectAll>>": self.select_all,
            "<Command-a>" if IS_MAC else "<Control-a>": self.select_all,
        }
        for sequence, action in keys.items():
            self.bind_key(sequence, action)

        gutter.bind("<Button-1>", self._on_gutter_click)
        gutter.bind("<Motion>", self._on_gutter_motion)
        gutter.bind("<Leave>", self._on_gutter_leave)
        ruler.bind("<Button-1>", self._on_ruler_press)
        ruler.bind("<B1-Motion>", self._on_ruler_drag)
        ruler.bind("<ButtonRelease-1>", self._on_ruler_release)
        ruler.bind("<Enter>", lambda _e: self._set_thumb_hover(True))
        ruler.bind("<Leave>", lambda _e: self._set_thumb_hover(False))

    def bind_key(self, sequence: str, action: Callable[[], object]) -> None:
        """Run *action* for a key pressed while the view has the focus."""

        def handler(_event: tk.Event[tk.Misc]) -> str:
            action()
            return "break"

        self._text.bind(sequence, handler)

    def _position_at(self, x: int, y: int) -> Position | None:
        if not self._rendered:
            return None
        row_text, col_text = self._text.index(f"@{x},{y}").split(".")
        i = min(int(row_text) - 1, len(self._rendered) - 1)
        content = self._rendered[i][1]
        return Position(self._first_row + i, min(int(col_text), len(content)))

    def _selection(self) -> tuple[Position, Position] | None:
        if self._anchor is None or self._head is None or self._anchor == self._head:
            return None
        return min(self._anchor, self._head), max(self._anchor, self._head)

    def _current_row(self) -> int:
        rows = self.session.rows
        if self._current is None:
            return self._top
        row = rows.row_of(self._current)
        return rows.nearest_row(self._current) if row is None else row

    def _move(self, delta: int, *, extend: bool = False) -> None:
        rows = self.session.rows
        total = len(rows)
        if not total:
            return
        row = self._current_row()
        target = max(0, min(total - 1, row + delta))
        if extend:
            if self._line_anchor is None:
                self._line_anchor = row
            first, last = sorted((self._line_anchor, target))
            self._anchor, self._head = Position(first, 0), Position(last, END)
        else:
            self.clear_selection()
        self._current = rows.line_at(target)
        self._reveal_row(target)
        self.refresh()

    def _home(self) -> None:
        self.clear_selection()
        if len(self.session.rows):
            self._current = self.session.rows.line_at(0)
        self._text.xview_moveto(0)
        self.scroll_to_row(0)

    def _end(self) -> None:
        self.clear_selection()
        rows = self.session.rows
        if len(rows):
            self._current = rows.line_at(len(rows) - 1)
        self.scroll_to_end()

    def _scroll_x(self, units: int) -> None:
        if self._text.cget("wrap") == "none":
            self._text.xview_scroll(units, "units")

    def _on_wheel(self, event: tk.Event[tk.Misc]) -> str:
        if event.num in (4, 5):
            units = -1.0 if event.num == 4 else 1.0
        else:
            units = -event.delta / (1 if IS_MAC else 120)
        if _modifiers(event) & CONTROL:
            self.on_zoom(1 if units < 0 else -1)
        elif _modifiers(event) & SHIFT:
            self._scroll_x(round(units * 4) or (1 if units > 0 else -1))
        else:
            self._wheel += units * WHEEL_ROWS
            steps = int(self._wheel)
            self._wheel -= steps
            if steps:
                self.scroll_rows(steps)
        return "break"

    def _on_press(self, event: tk.Event[tk.Misc]) -> str:
        self._text.focus_set()
        position = self._position_at(event.x, event.y)
        if position is not None:
            self._line_anchor = None
            self._anchor = self._head = position
            self._dragging = True
            self._current = self.session.rows.line_at(position.row)
            self.refresh()
        return "break"

    def _on_shift_press(self, event: tk.Event[tk.Misc]) -> str:
        position = self._position_at(event.x, event.y)
        if position is None:
            return "break"
        if self._anchor is None:
            self._anchor = Position(self._current_row(), 0)
        self._head = position
        self._dragging = True
        self.refresh()
        return "break"

    def _on_drag(self, event: tk.Event[tk.Misc]) -> str:
        if not self._dragging:
            return "break"
        self._pointer = (event.x, event.y)
        if 0 <= event.y < self._text.winfo_height():
            self._stop_autoscroll()
            position = self._position_at(event.x, event.y)
            if position is not None and position != self._head:
                self._head = position
                self.refresh()
        elif self._autoscroll_id is None:
            self._autoscroll()
        return "break"

    def _autoscroll(self) -> None:
        """Keep scrolling while a selection is dragged beyond the top or bottom edge."""
        _x, y = self._pointer
        height, line_height = self._text.winfo_height(), self._line_height()
        total = len(self.session.rows)
        if y < 0:
            self.scroll_rows(-max(1, int(-y / line_height)))
            self._head = Position(self._top, 0)
        else:
            self.scroll_rows(max(1, int((y - height) / line_height) + 1))
            self._head = Position(min(total - 1, self._top + self._full_rows), END)
        self._autoscroll_id = self.after(50, self._autoscroll)

    def _stop_autoscroll(self) -> None:
        if self._autoscroll_id is not None:
            self.after_cancel(self._autoscroll_id)
            self._autoscroll_id = None

    def _on_release(self, _event: tk.Event[tk.Misc]) -> str:
        self._dragging = False
        self._stop_autoscroll()
        return "break"

    def _on_double(self, event: tk.Event[tk.Misc]) -> str:
        position = self._position_at(event.x, event.y)
        if position is not None:
            content = self._rendered[position.row - self._first_row][1]
            for match in _WORD.finditer(content):
                if match.start() <= position.col <= match.end():
                    self._anchor = Position(position.row, match.start())
                    self._head = Position(position.row, match.end())
                    break
            self._dragging = False
            self.refresh()
        return "break"

    def _on_triple(self, event: tk.Event[tk.Misc]) -> str:
        position = self._position_at(event.x, event.y)
        if position is not None:
            self._anchor, self._head = Position(position.row, 0), Position(position.row, END)
            self._dragging = False
            self.refresh()
        return "break"

    def _on_context(self, event: tk.Event[tk.Misc]) -> str:
        self._text.focus_set()
        position = self._position_at(event.x, event.y)
        if position is None:
            return "break"
        rows = self.session.rows
        selection = self._selection()
        inside = selection is not None and selection[0] <= position <= selection[1]
        if not inside:
            self.clear_selection()
            self._current = rows.line_at(position.row)
        line = rows.line_at(position.row)
        text, lines = "", None
        if inside and selection is not None:
            if selection[0].row == selection[1].row:
                text = self.selection_text() or ""
            else:
                lines = self.selected_lines()
        else:
            content = self._rendered[position.row - self._first_row][1]
            word = next(
                (m for m in _WORD.finditer(content) if m.start() <= position.col <= m.end()), None
            )
            text = word.group() if word else ""
        self.refresh()
        info = ContextInfo(line, text.strip()[:200], lines, self.session.marks.at(line))
        self.on_context_menu(event, info)
        return "break"

    def _gutter_index(self, y: int) -> int | None:
        if not self._rendered:
            return None
        i = int(self._text.index(f"@0,{y}").split(".")[0]) - 1
        return i if 0 <= i < len(self._rendered) else None

    def _on_gutter_click(self, event: tk.Event[tk.Misc]) -> str:
        i = self._gutter_index(event.y)
        if i is not None:
            line = self._rendered[i][0]
            self.on_toggle_mark(line, bool(_modifiers(event) & SHIFT))
            self._current = line
            self.refresh()
        return "break"

    def _on_gutter_motion(self, event: tk.Event[tk.Misc]) -> None:
        i = self._gutter_index(event.y)
        if i != self._hover_row:
            self._hover_row = i
            self._draw_gutter()

    def _on_gutter_leave(self, _event: tk.Event[tk.Misc]) -> None:
        self._hover_row = None
        self._draw_gutter()

    def _set_thumb_hover(self, hover: bool) -> None:
        self._thumb_hover = hover
        self._draw_ruler()

    def _on_ruler_press(self, event: tk.Event[tk.Misc]) -> str:
        total, height = len(self.session.rows), self._ruler.winfo_height()
        bounds = self._thumb_bounds(height, total)
        if bounds is None:
            return "break"
        if bounds[0] <= event.y <= bounds[1]:
            self._thumb_grab = event.y - bounds[0]
        else:  # jump there, and keep dragging from the middle of the thumb
            self._thumb_grab = (bounds[1] - bounds[0]) / 2
            self._on_ruler_drag(event)
        self._draw_ruler()
        return "break"

    def _on_ruler_drag(self, event: tk.Event[tk.Misc]) -> str:
        if self._thumb_grab is not None:
            total, height = len(self.session.rows), max(1, self._ruler.winfo_height())
            self.scroll_to_row(int((event.y - self._thumb_grab) / height * total))
        return "break"

    def _on_ruler_release(self, _event: tk.Event[tk.Misc]) -> str:
        self._thumb_grab = None
        self._draw_ruler()
        return "break"


def _modifiers(event: tk.Event[tk.Misc]) -> int:
    return event.state if isinstance(event.state, int) else 0


def _innermost(marks: list[Mark], line: int) -> Mark | None:
    best: Mark | None = None
    for mark in marks:
        if mark.first <= line <= mark.last and (best is None or mark.size < best.size):
            best = mark
    return best


def _add_matches(indices: list[str], n: int, matches: Iterator[re.Match[str]]) -> None:
    """Add the non-empty matches on text line *n* to a tag's index list."""
    for count, match in enumerate(matches):
        if count >= MAX_MATCHES_PER_LINE:
            break
        start, end = match.start(), match.end()
        if end > start:
            indices += (f"{n}.{start}", f"{n}.{end}")
