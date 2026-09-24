"""Side panel with the highlight rules and the marks of the current file."""

from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from tkinter import ttk

from ..core.marks import Mark
from ..core.rules import RuleAction
from ..core.session import LogSession
from .theme import Fonts, Palette
from .widgets import Tooltip, swatch

_SPACES = re.compile(r"\s+")
# Leading timestamps make every preview look the same: skip them.
_TIMESTAMP = re.compile(
    r"^\W*(?:\d{4}-\d{2}-\d{2}[T ]?|\d{2}/\w{3}/\d{4}:|[A-Z][a-z]{2} +\d{1,2} +)?"
    r"\d{2}:\d{2}(?::\d{2})?(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]?\s*"
)


def _tool(master: tk.Misc, text: str, tip: str, command: Callable[[], object]) -> ttk.Button:
    button = ttk.Button(
        master, text=text, style="Surface.Toolbutton", command=command, takefocus=False
    )
    Tooltip(button, tip)
    return button


def _fit_column(tree: ttk.Treeview, column: str, texts: list[str], font: tkfont.Font) -> None:
    """Make a right-aligned column just wide enough, leaving the rest to the label."""
    widest = max((font.measure(text) for text in texts), default=0)
    tree.column(column, width=min(180, max(36, widest + 14)))


def _tree(master: tk.Misc, column: str, width: int) -> ttk.Treeview:
    tree = ttk.Treeview(master, columns=(column,), show="tree", selectmode="browse")
    tree.column("#0", stretch=True, width=150, minwidth=80)
    tree.column(column, width=width, minwidth=40, anchor="e", stretch=False)
    return tree


class RulesPanel(ttk.Frame):
    """Rules list: color, expression and number of matching lines."""

    def __init__(
        self, master: tk.Misc, session: LogSession, palette: Palette, font: tkfont.Font
    ) -> None:
        super().__init__(master, style="Surface.TFrame")
        self.session = session
        self._font = font
        self.on_add: Callable[[], None] = lambda: None
        self.on_edit: Callable[[int], None] = lambda index: None
        self.on_remove: Callable[[int], None] = lambda index: None
        self.on_toggle: Callable[[int], None] = lambda index: None
        self.on_move: Callable[[int, int], None] = lambda index, delta: None
        self.on_navigate: Callable[[int, bool], None] = lambda index, backwards: None
        self._counts: dict[str, str] = {}  # last count shown per row
        self._fitted: list[str] = []

        bar = ttk.Frame(self, style="Surface.TFrame", padding=(6, 6, 6, 2))
        bar.pack(fill="x")
        _tool(
            bar, "+ Add rule", "Highlight or hide lines matching an expression", self.on_add_click
        ).pack(side="left")
        _tool(
            bar,
            "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}",
            "Next line of the selected rule (Enter)",
            lambda: self._navigate(False),
        ).pack(side="right")
        _tool(
            bar,
            "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}",
            "Previous line (Shift+Enter)",
            lambda: self._navigate(True),
        ).pack(side="right")

        self.tree = _tree(self, "count", 76)
        self.tree.pack(fill="both", expand=True, padx=4)
        self.empty = ttk.Label(
            self,
            text="No rules yet.\nAdd one to color the lines that matter.",
            style="Surface.Faint.TLabel",
            justify="center",
        )

        footer = ttk.Frame(self, style="Surface.TFrame", padding=(6, 2, 6, 6))
        footer.pack(fill="x")
        for text, tip, command in (
            ("✎", "Edit (double-click)", lambda: self._with_selection(self.on_edit)),
            ("↑", "Move up: rules higher in the list win", lambda: self._move(-1)),
            ("↓", "Move down", lambda: self._move(1)),
            ("✕", "Remove (Delete)", lambda: self._with_selection(self.on_remove)),
        ):
            _tool(footer, text, tip, command).pack(side="left")
        ttk.Label(footer, text="Space: on/off", style="Surface.Faint.TLabel").pack(side="right")

        tree = self.tree
        tree.bind("<Double-Button-1>", self._on_double)
        tree.bind("<Return>", lambda _e: self._navigate(False))
        tree.bind("<Shift-Return>", lambda _e: self._navigate(True))
        tree.bind("<space>", lambda _e: self._with_selection(self.on_toggle))
        tree.bind("<Delete>", lambda _e: self._with_selection(self.on_remove))
        tree.bind("<BackSpace>", lambda _e: self._with_selection(self.on_remove))
        self.apply_palette(palette)

    def on_add_click(self) -> None:
        self.on_add()

    @property
    def selected_index(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def select(self, index: int) -> None:
        iid = str(index)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.tree.tag_configure("off", foreground=palette.faint)
        self.tree.tag_configure("hide", foreground=palette.muted)
        self.tree.tag_configure("error", foreground=palette.danger)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list from the session's rules."""
        tree, palette = self.tree, self._palette
        selected = self.selected_index
        tree.delete(*tree.get_children())
        self._counts.clear()
        rules = self.session.rules
        for index, rule in enumerate(rules):
            error = self.session.rule_error(index)
            hides = rule.action is RuleAction.HIDE
            color = palette.muted if hides else rule.color
            kind = "hollow" if not rule.enabled else "crossed" if hides else "fill"
            text = rule.query.text
            if hides:
                text = f"Hide: {text}"
            if error:
                text = f"⚠ {text}"
            tags = (
                ["error"] if error else ["off"] if not rule.enabled else ["hide"] if hides else []
            )
            count = self._counts[str(index)] = self._count(index)
            tree.insert(
                "",
                "end",
                iid=str(index),
                text=f"  {text}",
                image=swatch(tree, color, kind=kind),
                values=(count,),
                tags=tags,
            )
        self._fit_counts()
        if rules:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.3, anchor="center")
        if selected is not None and selected < len(rules):
            self.select(selected)

    def refresh_counts(self) -> None:
        for index in range(len(self.session.rules)):
            iid, count = str(index), self._count(index)
            if self._counts.get(iid) != count and self.tree.exists(iid):
                self._counts[iid] = count
                self.tree.set(iid, "count", count)
        self._fit_counts()

    def _fit_counts(self) -> None:
        texts = list(self._counts.values())
        if texts != self._fitted:
            self._fitted = texts
            _fit_column(self.tree, "count", texts, self._font)

    def _count(self, index: int) -> str:
        rules = self.session.rules
        if index >= len(rules):
            return ""
        if self.session.rule_error(index):
            return "invalid"
        scan = self.session.rule_scan(rules[index])
        if scan is None:
            return ""
        if scan.error:
            return "error"
        suffix = "" if scan.complete else "\N{HORIZONTAL ELLIPSIS}"  # still counting
        return f"{len(scan.lines):,}{suffix}"

    def _with_selection(self, action: Callable[[int], None]) -> str:
        index = self.selected_index
        if index is not None:
            action(index)
        return "break"

    def _move(self, delta: int) -> None:
        index = self.selected_index
        if index is not None:
            self.on_move(index, delta)

    def _navigate(self, backwards: bool) -> str:
        index = self.selected_index
        if index is None and self.session.rules:
            index = 0
            self.select(0)
        if index is not None:
            self.on_navigate(index, backwards)
        return "break"

    def _on_double(self, event: tk.Event[tk.Misc]) -> str:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.on_edit(int(iid))
        else:
            self.on_add()
        return "break"


class MarksPanel(ttk.Frame):
    """Marked lines and sections, in file order."""

    def __init__(
        self, master: tk.Misc, session: LogSession, palette: Palette, font: tkfont.Font
    ) -> None:
        super().__init__(master, style="Surface.TFrame")
        self.session = session
        self._font = font
        self.on_go: Callable[[Mark], None] = lambda mark: None
        self.on_edit: Callable[[Mark], None] = lambda mark: None
        self.on_remove: Callable[[Mark], None] = lambda mark: None
        self.on_export: Callable[[], None] = lambda: None
        self.on_clear: Callable[[], None] = lambda: None
        self.on_navigate: Callable[[bool], None] = lambda backwards: None
        self._marks: dict[str, Mark] = {}
        self._previews: dict[tuple[int, int], str] = {}

        bar = ttk.Frame(self, style="Surface.TFrame", padding=(6, 6, 6, 2))
        bar.pack(fill="x")
        _tool(
            bar, "Export…", "Save the marked lines as a Markdown report", lambda: self.on_export()
        ).pack(side="left")
        _tool(
            bar,
            "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}",
            "Next mark (F2)",
            lambda: self.on_navigate(False),
        ).pack(side="right")
        _tool(
            bar,
            "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}",
            "Previous mark (Shift+F2)",
            lambda: self.on_navigate(True),
        ).pack(side="right")

        self.tree = _tree(self, "lines", 92)
        self.tree.pack(fill="both", expand=True, padx=4)
        self.empty = ttk.Label(
            self,
            text=(
                "No marks yet.\n\nClick a line number to mark a line,\n"
                "Shift+click another to extend it\ninto a section, or press Ctrl+M."
            ),
            style="Surface.Faint.TLabel",
            justify="center",
        )

        footer = ttk.Frame(self, style="Surface.TFrame", padding=(6, 2, 6, 6))
        footer.pack(fill="x")
        _tool(footer, "✎", "Edit label and color", lambda: self._with_selection(self.on_edit)).pack(
            side="left"
        )
        _tool(footer, "✕", "Remove (Delete)", lambda: self._with_selection(self.on_remove)).pack(
            side="left"
        )
        _tool(footer, "Clear all", "Remove every mark", lambda: self.on_clear()).pack(side="right")

        tree = self.tree
        tree.bind("<Double-Button-1>", lambda _e: self._with_selection(self.on_go))
        tree.bind("<Return>", lambda _e: self._with_selection(self.on_go))
        tree.bind("<Delete>", lambda _e: self._with_selection(self.on_remove))
        tree.bind("<BackSpace>", lambda _e: self._with_selection(self.on_remove))
        self._palette = palette

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.refresh()

    def refresh(self) -> None:
        tree = self.tree
        selected = tree.selection()
        tree.delete(*tree.get_children())
        self._marks = {}
        file = self.session.file
        for mark in self.session.marks:
            iid = str(mark.id)
            self._marks[iid] = mark
            tree.insert(
                "",
                "end",
                iid=iid,
                text=f"  {mark.label or self._preview(mark)}",
                image=swatch(tree, mark.color),
                values=(mark.describe_lines(),),
            )
        _fit_column(tree, "lines", [m.describe_lines() for m in self.session.marks], self._font)
        if self._marks or file is None:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.35, anchor="center")
        for iid in selected:
            if tree.exists(iid):
                tree.selection_set(iid)

    def select(self, mark: Mark) -> None:
        iid = str(mark.id)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def _preview(self, mark: Mark) -> str:
        key = (mark.id, mark.first)
        if key not in self._previews and self.session.file is not None:
            try:
                text = self.session.file.read_line(mark.first)
            except (OSError, IndexError):
                text = ""
            text = _TIMESTAMP.sub("", _SPACES.sub(" ", text).strip(), count=1)
            self._previews[key] = text[:120] or "(empty line)"
        return self._previews.get(key, "")

    def _with_selection(self, action: Callable[[Mark], None]) -> str:
        selection = self.tree.selection()
        if selection and selection[0] in self._marks:
            action(self._marks[selection[0]])
        return "break"


class SidePanel(ttk.Frame):
    """Switch between the rules and the marks."""

    def __init__(
        self, master: tk.Misc, session: LogSession, palette: Palette, fonts: Fonts
    ) -> None:
        super().__init__(master, style="Surface.TFrame")
        self.page = tk.StringVar(self, "rules")
        header = ttk.Frame(self, style="Surface.TFrame", padding=(6, 8, 6, 4))
        header.pack(fill="x")
        self._tabs: dict[str, ttk.Radiobutton] = {}
        for value, text in (("rules", "Highlights"), ("marks", "Marks")):
            tab = ttk.Radiobutton(
                header,
                text=text,
                value=value,
                variable=self.page,
                style="Surface.Toolbutton",
                command=self._show,
                takefocus=False,
            )
            tab.pack(side="left", padx=(0, 2))
            self._tabs[value] = tab
        ttk.Separator(self).pack(fill="x")
        self.rules = RulesPanel(self, session, palette, fonts.ui)
        self.marks = MarksPanel(self, session, palette, fonts.ui)
        self._session = session
        self._show()

    def show(self, page: str) -> None:
        self.page.set(page)
        self._show()

    def update_titles(self) -> None:
        count = len(self._session.marks)
        self._tabs["marks"].configure(text=f"Marks  {count}" if count else "Marks")

    def apply_palette(self, palette: Palette) -> None:
        self.rules.apply_palette(palette)
        self.marks.apply_palette(palette)

    def _show(self) -> None:
        showing, hidden = (
            (self.rules, self.marks) if self.page.get() == "rules" else (self.marks, self.rules)
        )
        hidden.pack_forget()
        showing.pack(fill="both", expand=True)
