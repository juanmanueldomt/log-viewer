"""The search field with its options, result counter, navigation and filter toggle."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from functools import partial
from tkinter import ttk

from ..core.query import Query
from ..core.settings import Settings
from .theme import Fonts, Palette
from .widgets import Placeholder, Tooltip

TYPING_DELAY_MS = 180


class SearchBar(ttk.Frame):
    """Incremental search: results update while typing."""

    def __init__(
        self,
        master: tk.Misc,
        settings: Settings,
        fonts: Fonts,
        palette: Palette,
        filter_variable: tk.BooleanVar,
    ) -> None:
        super().__init__(master)
        self.on_change: Callable[[Query | None], None] = lambda query: None
        self.on_next: Callable[[bool], None] = lambda backwards: None
        self.on_filter: Callable[[bool], None] = lambda enabled: None
        self.on_escape: Callable[[], None] = lambda: None

        self._settings = settings
        self._fonts = fonts
        self._after: str | None = None
        self._history_index = -1
        self._status = ("", False)
        self.text = tk.StringVar(self)
        self.case_sensitive = tk.BooleanVar(self, settings.search_case_sensitive)
        self.whole_word = tk.BooleanVar(self, settings.search_whole_word)
        self.regex = tk.BooleanVar(self, settings.search_regex)
        self.filter = filter_variable

        self.entry = ttk.Entry(self, textvariable=self.text, width=34, font=fonts.ui)
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._placeholder = Placeholder(self.entry, self.text, "Search")
        self._placeholder.apply_palette(palette, fonts.ui)

        options = (
            ("Aa", self.case_sensitive, "Match case (Alt+C)", "Toolbutton"),
            ("ab", self.whole_word, "Match whole word (Alt+W)", "Underline.Toolbutton"),
            (".*", self.regex, "Use regular expression (Alt+R)", "Toolbutton"),
        )
        for column, (label, variable, tip, style) in enumerate(options, start=1):
            button = ttk.Checkbutton(
                self,
                text=label,
                variable=variable,
                style=style,
                takefocus=False,
                command=self._options_changed,
                width=3,
            )
            button.grid(row=0, column=column)
            Tooltip(button, tip)

        self.counter = ttk.Label(self, style="Muted.TLabel", width=17, anchor="center")
        self.counter.grid(row=0, column=4, padx=4)
        for column, (label, tip, backwards) in enumerate(
            (("↑", "Previous match (Shift+Enter)", True), ("↓", "Next match (Enter)", False)),
            start=5,
        ):
            arrow = ttk.Button(
                self,
                text=label,
                style="Toolbutton",
                width=2,
                takefocus=False,
                command=partial(self._navigate, backwards),
            )
            arrow.grid(row=0, column=column)
            Tooltip(arrow, tip)
        self.filter_button = ttk.Checkbutton(
            self,
            text="Filter",
            variable=self.filter,
            style="Toolbutton",
            takefocus=False,
            command=lambda: self.on_filter(self.filter.get()),
        )
        self.filter_button.grid(row=0, column=7, padx=(6, 0))
        Tooltip(self.filter_button, "Show only the lines that match (Ctrl+Shift+F)")
        self.columnconfigure(0, weight=1)

        self.text.trace_add("write", lambda *_: self._schedule())
        entry = self.entry
        entry.bind("<Return>", lambda _e: self._submit(backwards=False))
        entry.bind("<KP_Enter>", lambda _e: self._submit(backwards=False))
        entry.bind("<Shift-Return>", lambda _e: self._submit(backwards=True))
        entry.bind("<Escape>", self._on_escape)
        entry.bind("<Up>", lambda _e: self._recall(1))
        entry.bind("<Down>", lambda _e: self._recall(-1))
        for key, variable in (
            ("c", self.case_sensitive),
            ("w", self.whole_word),
            ("r", self.regex),
        ):
            entry.bind(f"<Alt-{key}>", partial(self._toggle, variable))

    # -- public API -----------------------------------------------------------

    def query(self) -> Query | None:
        text = self.text.get()
        if not text:
            return None
        return Query(text, self.regex.get(), self.case_sensitive.get(), self.whole_word.get())

    def set_query(self, text: str, *, regex: bool = False) -> None:
        """Replace the search (e.g. from the context menu) and apply it at once."""
        self.regex.set(regex)
        self.text.set(text)
        self._apply()

    def focus_entry(self) -> None:
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self.entry.icursor("end")

    def set_status(self, text: str, *, error: bool = False) -> None:
        if (text, error) != self._status:  # skip no-op Tk calls: this runs on every redraw
            self._status = (text, error)
            self.counter.configure(
                text=text, style="Error.Status.TLabel" if error else "Muted.TLabel"
            )
            self.entry.configure(style="Error.TEntry" if error else "TEntry")

    def apply_palette(self, palette: Palette) -> None:
        self._placeholder.apply_palette(palette, self._fonts.ui)

    def destroy(self) -> None:
        if self._after is not None:
            self.after_cancel(self._after)
            self._after = None
        super().destroy()

    def remember(self) -> None:
        """Add the current text to the search history."""
        self._settings.remember_search(self.text.get())
        self._history_index = -1

    # -- internals ------------------------------------------------------------

    def _schedule(self) -> None:
        if self._after is not None:
            self.after_cancel(self._after)
        self._after = self.after(TYPING_DELAY_MS, self._apply)

    def _apply(self) -> None:
        if self._after is not None:
            self.after_cancel(self._after)
            self._after = None
        self.on_change(self.query())

    def _options_changed(self) -> None:
        settings = self._settings
        settings.search_case_sensitive = self.case_sensitive.get()
        settings.search_whole_word = self.whole_word.get()
        settings.search_regex = self.regex.get()
        self._apply()

    def _navigate(self, backwards: bool) -> None:
        self.on_next(backwards)

    def _toggle(self, variable: tk.BooleanVar, _event: object = None) -> str:
        variable.set(not variable.get())
        self._options_changed()
        return "break"

    def _submit(self, *, backwards: bool) -> str:
        if self._after is not None:  # typed quickly then pressed Enter
            self._apply()
        self.remember()
        self.on_next(backwards)
        return "break"

    def _on_escape(self, _event: tk.Event[tk.Misc]) -> str:
        if self.text.get():
            self.text.set("")
            self._apply()
        self.on_escape()
        return "break"

    def _recall(self, step: int) -> str:
        history = self._settings.search_history
        if not history:
            return "break"
        self._history_index = max(-1, min(len(history) - 1, self._history_index + step))
        self.text.set(history[self._history_index] if self._history_index >= 0 else "")
        self.entry.icursor("end")
        return "break"
