"""Modal dialogs: highlight rules, marks, go to line and the shortcut reference."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Sequence
from tkinter import ttk
from typing import Generic, TypeVar

from ..core.marks import MARK_COLORS, Mark
from ..core.query import Query, QueryError
from ..core.rules import RULE_COLORS, Rule, RuleAction
from .theme import Fonts, Palette
from .widgets import ColorPicker

T = TypeVar("T")


class Dialog(tk.Toplevel, Generic[T]):
    """A small modal window closed with its buttons, Enter or Escape."""

    def __init__(self, parent: tk.Misc, title: str, palette: Palette) -> None:
        super().__init__(parent)
        self.withdraw()  # shown, centred, by show()
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.configure(background=palette.window)
        self.palette = palette
        self.result: T | None = None
        self.body = ttk.Frame(self, padding=(20, 18, 20, 6))
        self.body.pack(fill="both", expand=True)
        self.body.columnconfigure(0, weight=1)
        self._buttons = ttk.Frame(self, padding=(20, 10, 20, 18))
        self._buttons.pack(fill="x")
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _e: self.cancel())

    def add_buttons(self, ok_text: str, *, cancel: bool = True) -> None:
        ttk.Button(self._buttons, text=ok_text, style="Accent.TButton", command=self.ok).pack(
            side="right"
        )
        if cancel:
            ttk.Button(self._buttons, text="Cancel", command=self.cancel).pack(
                side="right", padx=(0, 8)
            )
        self.bind("<Return>", lambda _e: self.ok())
        self.bind("<KP_Enter>", lambda _e: self.ok())

    def show(self, focus: tk.Widget | None = None) -> T | None:
        """Display the dialog and wait until it is closed; return its result."""
        self.update_idletasks()
        parent = self.master.winfo_toplevel()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        with contextlib.suppress(tk.TclError):  # grabbing can fail if another app holds it
            self.wait_visibility()
            self.grab_set()
        (focus or self).focus_set()
        self.wait_window()
        return self.result

    def build_result(self) -> T | None:
        """The dialog's result, or ``None`` to keep it open (invalid input)."""
        raise NotImplementedError

    def ok(self) -> None:
        result = self.build_result()
        if result is not None:
            self.result = result
            self.destroy()

    def cancel(self) -> None:
        self.result = None
        self.destroy()

    def label(self, text: str, row: int, *, pady: tuple[int, int] = (0, 4)) -> ttk.Label:
        widget = ttk.Label(self.body, text=text)
        widget.grid(row=row, column=0, sticky="w", pady=pady)
        return widget


class RuleDialog(Dialog[Rule]):
    """Create or edit a highlight rule."""

    def __init__(self, parent: tk.Misc, palette: Palette, rule: Rule | None, color: str) -> None:
        super().__init__(parent, "Edit rule" if rule else "New rule", palette)
        rule = rule or Rule(Query(""), color=color)
        self._enabled = rule.enabled
        self.pattern = tk.StringVar(self, rule.query.text)
        self.regex = tk.BooleanVar(self, rule.query.regex)
        self.case_sensitive = tk.BooleanVar(self, rule.query.case_sensitive)
        self.whole_word = tk.BooleanVar(self, rule.query.whole_word)
        self.action = tk.StringVar(self, rule.action.value)

        self.label("Expression", 0)
        self.entry = ttk.Entry(self.body, textvariable=self.pattern, width=46)
        self.entry.grid(row=1, column=0, sticky="ew")
        self.error = ttk.Label(self.body, style="Error.Status.TLabel", padding=0)
        self.error.grid(row=2, column=0, sticky="w", pady=(2, 0))
        options = ttk.Frame(self.body)
        options.grid(row=3, column=0, sticky="w", pady=(4, 14))
        for text, variable in (
            ("Regular expression", self.regex),
            ("Match case", self.case_sensitive),
            ("Whole word", self.whole_word),
        ):
            ttk.Checkbutton(options, text=text, variable=variable, command=self._validate).pack(
                side="left", padx=(0, 16)
            )
        self.label("When a line matches", 4)
        actions = ttk.Frame(self.body)
        actions.grid(row=5, column=0, sticky="w", pady=(0, 14))
        for action in RuleAction:
            ttk.Radiobutton(
                actions,
                text=action.label,
                value=action.value,
                variable=self.action,
                command=self._action_changed,
            ).pack(side="left", padx=(0, 16))
        self._color_label = self.label("Color", 6)
        self.colors = ColorPicker(self.body, RULE_COLORS, rule.color, palette)
        self.colors.grid(row=7, column=0, sticky="w", pady=(0, 6))
        self.pattern.trace_add("write", lambda *_: self._validate())
        self.add_buttons("Save")
        self._action_changed()
        self._validate()

    def show(self, focus: tk.Widget | None = None) -> Rule | None:
        return super().show(focus or self.entry)

    def _query(self) -> Query:
        return Query(
            self.pattern.get(), self.regex.get(), self.case_sensitive.get(), self.whole_word.get()
        )

    def _validate(self) -> bool:
        query = self._query()
        message = ""
        if query.text:
            try:
                query.validate()
            except QueryError as exc:
                message = str(exc)
        self.error.configure(text=message)
        self.entry.configure(style="Error.TEntry" if message else "TEntry")
        return bool(query.text) and not message

    def _action_changed(self) -> None:
        hides = self.action.get() == RuleAction.HIDE.value
        for widget in (self._color_label, self.colors):
            if hides:
                widget.grid_remove()
            else:
                widget.grid()

    def build_result(self) -> Rule | None:
        if not self._validate():
            if not self.pattern.get():
                self.error.configure(text="Enter the text or regular expression to look for.")
            self.entry.focus_set()
            return None
        return Rule(
            self._query(),
            color=self.colors.get(),
            action=RuleAction(self.action.get()),
            enabled=self._enabled,
        )


class MarkDialog(Dialog[tuple[str, str]]):
    """Label and color of a mark."""

    def __init__(self, parent: tk.Misc, palette: Palette, mark: Mark) -> None:
        super().__init__(parent, "Edit mark", palette)
        plural = "s" if mark.size > 1 else ""
        ttk.Label(
            self.body, text=f"Line{plural} {mark.describe_lines()}", style="Muted.TLabel"
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.label("Label", 1)
        self.text = tk.StringVar(self, mark.label)
        self.entry = ttk.Entry(self.body, textvariable=self.text, width=40)
        self.entry.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        self.entry.select_range(0, "end")
        self.label("Color", 3)
        self.colors = ColorPicker(self.body, MARK_COLORS, mark.color, palette)
        self.colors.grid(row=4, column=0, sticky="w", pady=(0, 6))
        self.add_buttons("Save")

    def show(self, focus: tk.Widget | None = None) -> tuple[str, str] | None:
        return super().show(focus or self.entry)

    def build_result(self) -> tuple[str, str] | None:
        return self.text.get().strip(), self.colors.get()


class GoToLineDialog(Dialog[int]):
    """Ask for a (1-based) line number; the result is 0-based."""

    def __init__(self, parent: tk.Misc, palette: Palette, line_count: int, current: int) -> None:
        super().__init__(parent, "Go to line", palette)
        self._line_count = line_count
        self.label(f"Line number (1\N{EN DASH}{line_count:,})", 0)
        self.value = tk.StringVar(self, str(current + 1))
        self.entry = ttk.Entry(self.body, textvariable=self.value, width=28)
        self.entry.grid(row=1, column=0, sticky="ew")
        self.entry.select_range(0, "end")
        self.error = ttk.Label(self.body, style="Error.Status.TLabel", padding=0)
        self.error.grid(row=2, column=0, sticky="w", pady=(2, 4))
        self.add_buttons("Go")

    def show(self, focus: tk.Widget | None = None) -> int | None:
        return super().show(focus or self.entry)

    def build_result(self) -> int | None:
        text = self.value.get().strip().replace(",", "").replace("_", "").replace(".", "")
        try:
            number = int(text)
        except ValueError:
            number = 0
        if not 1 <= number <= self._line_count:
            self.error.configure(text=f"Enter a number from 1 to {self._line_count:,}.")
            self.entry.configure(style="Error.TEntry")
            return None
        return number - 1


class ShortcutsDialog(Dialog[bool]):
    """Reference card of keyboard and mouse shortcuts."""

    def __init__(
        self,
        parent: tk.Misc,
        palette: Palette,
        fonts: Fonts,
        groups: Sequence[tuple[str, Sequence[tuple[str, str]]]],
    ) -> None:
        super().__init__(parent, "Keyboard shortcuts", palette)
        half = -(-len(groups) // 2)
        for column, chunk in enumerate((groups[:half], groups[half:])):
            frame = ttk.Frame(self.body)  # one grid per column keeps descriptions aligned
            frame.grid(row=0, column=column, sticky="nw", padx=(0, 32) if column == 0 else 0)
            row = 0
            for title, entries in chunk:
                ttk.Label(frame, text=title, font=fonts.bold).grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=(10 if row else 0, 4)
                )
                row += 1
                for keys, description in entries:
                    ttk.Label(frame, text=keys, style="Muted.TLabel", font=fonts.small).grid(
                        row=row, column=0, sticky="w", padx=(0, 16)
                    )
                    ttk.Label(frame, text=description).grid(row=row, column=1, sticky="w")
                    row += 1
        self.add_buttons("Close", cancel=False)

    def build_result(self) -> bool | None:
        return True
