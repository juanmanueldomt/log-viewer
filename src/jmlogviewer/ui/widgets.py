"""Small reusable widgets: tooltips, colour swatches, colour picker, placeholders."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import colorchooser, ttk
from typing import Any

from .theme import LIGHT, Palette, blend

_swatches: dict[tuple[str, str, int, str], tk.PhotoImage] = {}


def swatch(master: tk.Misc, color: str, *, size: int = 12, kind: str = "fill") -> tk.PhotoImage:
    """A small colour sample image, cached.

    *kind* is ``"fill"`` (solid), ``"hollow"`` (outline: disabled) or
    ``"crossed"`` (outline with a diagonal: lines are hidden).
    """
    key = (str(master.winfo_toplevel()), color, size, kind)
    image = _swatches.get(key)
    if image is not None:
        return image
    image = tk.PhotoImage(master=master, width=size, height=size)
    edge = size - 1
    if kind == "fill":
        image.put(color, to=(0, 0, size, size))
    else:
        for box in (
            (0, 0, size, 2),
            (0, edge - 1, size, size),
            (0, 0, 2, size),
            (edge - 1, 0, size, size),
        ):
            image.put(color, to=box)
        if kind == "crossed":
            for i in range(size):
                image.put(color, to=(i, i, min(size, i + 2), min(size, i + 1)))
    _swatches[key] = image
    return image


class Tooltip:
    """A hint shown when the pointer rests on a widget."""

    palette: Palette = LIGHT  # shared; updated when the theme changes
    font: Any = "TkDefaultFont"

    def __init__(self, widget: tk.Widget, text: str | Callable[[], str], delay: int = 500) -> None:
        self._widget = widget
        self._text = text
        self._delay = delay
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event: object = None) -> None:
        self._cancel()
        self._after = self._widget.after(self._delay, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            with contextlib.suppress(tk.TclError):
                self._widget.after_cancel(self._after)
            self._after = None

    def _show(self) -> None:
        self._after = None
        text = self._text() if callable(self._text) else self._text
        if not text or not self._widget.winfo_viewable():
            return
        palette = Tooltip.palette
        tip = tk.Toplevel(self._widget)
        tip.wm_overrideredirect(True)
        with contextlib.suppress(tk.TclError):
            tip.wm_attributes("-type", "tooltip")  # X11 window type hint
        tk.Label(
            tip,
            text=text,
            background=palette.tooltip,
            foreground=palette.tooltip_text,
            font=Tooltip.font,
            justify="left",
            padx=8,
            pady=4,
        ).pack()
        tip.update_idletasks()
        x = self._widget.winfo_rootx()
        x = max(0, min(x, tip.winfo_screenwidth() - tip.winfo_reqwidth() - 4))
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 6
        tip.wm_geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self, _event: object = None) -> None:
        self._cancel()
        if self._tip is not None:
            with contextlib.suppress(tk.TclError):
                self._tip.destroy()
            self._tip = None


class Placeholder:
    """Faint hint text shown inside an empty entry."""

    def __init__(self, entry: ttk.Entry, variable: tk.StringVar, text: str) -> None:
        self._variable = variable
        self._label = tk.Label(entry, text=text, cursor="xterm", borderwidth=0, padx=0, pady=0)
        self._label.bind("<Button-1>", lambda _event: entry.focus_set())
        variable.trace_add("write", self._update)
        self._update()

    def apply_palette(self, palette: Palette, font: Any) -> None:
        self._label.configure(background=palette.surface, foreground=palette.faint, font=font)

    def _update(self, *_args: object) -> None:
        if self._variable.get():
            self._label.place_forget()
        else:
            self._label.place(x=7, rely=0.5, anchor="w")


class ColorPicker(ttk.Frame):
    """Choose one of a few preset colours, or any colour with the system picker."""

    SIZE = 20
    GAP = 8

    def __init__(
        self, master: tk.Misc, colors: Sequence[str], value: str, palette: Palette
    ) -> None:
        super().__init__(master)
        self._presets = list(colors)
        self._value = value
        self._palette = palette
        self._canvas = tk.Canvas(
            self,
            height=self.SIZE + 8,
            highlightthickness=0,
            borderwidth=0,
            background=palette.window,
            takefocus=True,
            cursor="hand2",
        )
        self._canvas.pack(side="left")
        ttk.Button(self, text="Custom…", command=self._choose_custom).pack(
            side="left", padx=(self.GAP, 0)
        )
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Left>", lambda _e: self._move(-1))
        self._canvas.bind("<Right>", lambda _e: self._move(1))
        self._canvas.bind("<FocusIn>", lambda _e: self._draw())
        self._canvas.bind("<FocusOut>", lambda _e: self._draw())
        self._draw()

    def get(self) -> str:
        return self._value

    def set(self, color: str) -> None:
        self._value = color
        self._draw()

    @property
    def _colors(self) -> list[str]:
        known = {c.upper() for c in self._presets}
        return self._presets + ([] if self._value.upper() in known else [self._value])

    def _draw(self) -> None:
        canvas, size, gap = self._canvas, self.SIZE, self.GAP
        canvas.delete("all")
        colors = self._colors
        canvas.configure(width=len(colors) * (size + gap) + 4)
        focused = self.focus_get() is canvas
        for i, color in enumerate(colors):
            x, y = 4 + i * (size + gap), 4
            if color.upper() == self._value.upper():
                ring = self._palette.accent if focused else self._palette.text
                canvas.create_oval(x - 3, y - 3, x + size + 3, y + size + 3, outline=ring, width=2)
            canvas.create_oval(
                x, y, x + size, y + size, fill=color, outline=blend("#000000", color, 0.15)
            )

    def _on_click(self, event: tk.Event[tk.Misc]) -> None:
        self._canvas.focus_set()
        index = int((event.x - 2) // (self.SIZE + self.GAP))
        colors = self._colors
        if 0 <= index < len(colors):
            self.set(colors[index])

    def _move(self, step: int) -> None:
        colors = [c.upper() for c in self._colors]
        index = colors.index(self._value.upper()) if self._value.upper() in colors else 0
        self.set(self._colors[(index + step) % len(colors)])

    def _choose_custom(self) -> None:
        _rgb, color = colorchooser.askcolor(
            color=self._value, parent=self.winfo_toplevel(), title="Choose a colour"
        )
        if color:
            self.set(str(color).upper())


def app_icon(master: tk.Misc, accent: str) -> tk.PhotoImage:
    """The window icon, drawn at runtime: lines of text on a rounded tile."""
    size = 32
    image = tk.PhotoImage(master=master, width=size, height=size)
    for box in ((4, 2, 28, 30), (2, 4, 30, 28), (3, 3, 29, 29)):  # rounded corners
        image.put(accent, to=box)
    for top, right, color in (
        (8, 25, "#FFFFFF"),
        (13, 21, "#FFFFFF"),
        (18, 25, "#FFC53D"),
        (23, 18, "#FFFFFF"),
    ):
        image.put(color, to=(7, top, right, top + 2))
    return image
