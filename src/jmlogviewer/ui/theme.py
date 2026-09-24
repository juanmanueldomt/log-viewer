"""Colours, fonts and ttk styling for a flat, minimal look in light and dark."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk

MONOSPACE_FAMILIES = (
    "JetBrains Mono",
    "Cascadia Mono",
    "SF Mono",
    "Menlo",
    "Consolas",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Noto Sans Mono",
    "Ubuntu Mono",
    "Courier New",
)


@dataclass(frozen=True, slots=True)
class Palette:
    name: str
    dark: bool
    window: str  # toolbar, status bar and dialog background
    surface: str  # side panel, inputs, menus
    view: str  # log background
    border: str
    text: str
    muted: str
    faint: str  # line numbers, placeholders
    accent: str
    accent_text: str
    accent_soft: str  # selected toggles and list rows
    hover: str
    selection: str
    current_line: str
    search: str
    search_current: str
    thumb: str
    thumb_active: str
    danger: str
    tooltip: str
    tooltip_text: str


LIGHT = Palette(
    name="light",
    dark=False,
    window="#F6F7F9",
    surface="#FFFFFF",
    view="#FFFFFF",
    border="#DFE3E8",
    text="#1F2328",
    muted="#59636E",
    faint="#8C959F",
    accent="#0969DA",
    accent_text="#FFFFFF",
    accent_soft="#DCEBFE",
    hover="#EAEEF2",
    selection="#B6D6FF",
    current_line="#F2F5F9",
    search="#FFE58F",
    search_current="#FF9F43",
    thumb="#DCE1E6",
    thumb_active="#B4BCC5",
    danger="#CF222E",
    tooltip="#24292F",
    tooltip_text="#FFFFFF",
)

DARK = Palette(
    name="dark",
    dark=True,
    window="#1B1F24",
    surface="#22272E",
    view="#16191D",
    border="#30363D",
    text="#E6EDF3",
    muted="#9DA7B3",
    faint="#6E7681",
    accent="#4493F8",
    accent_text="#FFFFFF",
    accent_soft="#1D3657",
    hover="#2B3139",
    selection="#264F78",
    current_line="#1F242B",
    search="#C9A227",
    search_current="#F0883E",
    thumb="#343B44",
    thumb_active="#4C5561",
    danger="#F85149",
    tooltip="#E6EDF3",
    tooltip_text="#1B1F24",
)

PALETTES = {palette.name: palette for palette in (LIGHT, DARK)}


# -- colour arithmetic --------------------------------------------------------


def _rgb(color: str) -> tuple[int, int, int]:
    try:
        return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    except (ValueError, IndexError):
        return 128, 128, 128


def blend(color: str, background: str, alpha: float) -> str:
    """*color* painted with opacity *alpha* over *background*."""
    fg, bg = _rgb(color), _rgb(background)
    mixed = (round(f * alpha + b * (1 - alpha)) for f, b in zip(fg, bg, strict=True))
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def luminance(color: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def readable_on(background: str) -> str:
    """Black or white, whichever reads better on *background*."""
    return "#111111" if luminance(background) > 0.36 else "#FFFFFF"


def line_tint(color: str, palette: Palette) -> str:
    """Background for a whole highlighted line: a soft tint of *color*."""
    return blend(color, palette.view, 0.32 if palette.dark else 0.2)


def match_tint(color: str, palette: Palette) -> str:
    """Background for highlighted matching text: stronger than a line tint."""
    return blend(color, palette.view, 0.7 if palette.dark else 0.5)


def mark_tint(color: str, palette: Palette) -> str:
    """Background of lines inside a mark: subtle, so rule tints stay readable."""
    return blend(color, palette.view, 0.16 if palette.dark else 0.1)


# -- fonts --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fonts:
    ui: tkfont.Font
    bold: tkfont.Font
    small: tkfont.Font
    title: tkfont.Font
    underline: tkfont.Font
    mono: tkfont.Font
    mono_bold: tkfont.Font

    @classmethod
    def create(cls, root: tk.Misc, mono_size: int) -> Fonts:
        families = set(tkfont.families(root))
        fixed = tkfont.nametofont("TkFixedFont", root).actual("family")
        mono_family = next((f for f in MONOSPACE_FAMILIES if f in families), fixed)
        default = tkfont.nametofont("TkDefaultFont", root).actual()
        family, size = default["family"], abs(int(default["size"])) or 10
        return cls(
            ui=tkfont.Font(root, family=family, size=size),
            bold=tkfont.Font(root, family=family, size=size, weight="bold"),
            small=tkfont.Font(root, family=family, size=max(7, size - 1)),
            title=tkfont.Font(root, family=family, size=size + 8, weight="bold"),
            underline=tkfont.Font(root, family=family, size=size, underline=True),
            mono=tkfont.Font(root, family=mono_family, size=mono_size),
            mono_bold=tkfont.Font(root, family=mono_family, size=mono_size, weight="bold"),
        )

    def set_mono_size(self, size: int) -> None:
        self.mono.configure(size=size)
        self.mono_bold.configure(size=size)


# -- ttk ----------------------------------------------------------------------


def apply_theme(root: tk.Tk, palette: Palette, fonts: Fonts) -> None:
    """Style every ttk widget and the classic Tk option database."""
    p = palette
    style = ttk.Style(root)
    style.theme_use("clam")  # the most customisable built-in theme on every platform

    style.configure(
        ".",
        background=p.window,
        foreground=p.text,
        bordercolor=p.border,
        lightcolor=p.window,
        darkcolor=p.window,
        troughcolor=p.window,
        focuscolor=p.accent,
        selectbackground=p.accent_soft,
        selectforeground=p.text,
        insertcolor=p.text,
        fieldbackground=p.surface,
        font=fonts.ui,
    )
    style.map(".", foreground=[("disabled", p.faint)])

    for prefix, background in (("", p.window), ("Surface.", p.surface), ("View.", p.view)):
        style.configure(f"{prefix}TFrame", background=background)
        style.configure(f"{prefix}TLabel", background=background, foreground=p.text)
        style.configure(f"{prefix}Muted.TLabel", background=background, foreground=p.muted)
        style.configure(f"{prefix}Faint.TLabel", background=background, foreground=p.faint)
        style.configure(f"{prefix}Link.TLabel", background=background, foreground=p.accent)
    style.configure("Title.TLabel", font=fonts.title)
    style.configure("View.Title.TLabel", background=p.view, font=fonts.title)
    style.configure("Status.TLabel", foreground=p.muted, font=fonts.small, padding=(6, 0))
    style.configure("Error.Status.TLabel", foreground=p.danger)
    style.configure("Accent.Status.TLabel", foreground=p.accent)
    style.configure("Heading.TLabel", background=p.surface, foreground=p.muted, font=fonts.bold)

    style.configure(
        "TButton",
        padding=(12, 4),
        background=p.surface,
        bordercolor=p.border,
        lightcolor=p.surface,
        darkcolor=p.surface,
        focusthickness=1,
        focuscolor=p.accent,
    )
    style.map(
        "TButton",
        background=[("pressed", p.border), ("active", p.hover)],
        lightcolor=[("pressed", p.border), ("active", p.hover)],
        darkcolor=[("pressed", p.border), ("active", p.hover)],
        bordercolor=[("focus", p.accent)],
    )
    style.configure(
        "Accent.TButton",
        background=p.accent,
        foreground=p.accent_text,
        bordercolor=p.accent,
        lightcolor=p.accent,
        darkcolor=p.accent,
        focuscolor=p.accent_text,
    )
    hover_accent = blend("#000000", p.accent, 0.12)
    style.map(
        "Accent.TButton",
        background=[("pressed", hover_accent), ("active", hover_accent)],
        lightcolor=[("pressed", hover_accent), ("active", hover_accent)],
        darkcolor=[("pressed", hover_accent), ("active", hover_accent)],
    )

    for prefix, background in (("", p.window), ("Surface.", p.surface)):
        name = f"{prefix}Toolbutton"
        style.configure(
            name,
            padding=(7, 3),
            background=background,
            bordercolor=background,
            lightcolor=background,
            darkcolor=background,
            anchor="center",
        )
        style.map(
            name,
            background=[("selected", p.accent_soft), ("pressed", p.border), ("active", p.hover)],
            bordercolor=[("selected", p.accent_soft), ("active", p.hover)],
            lightcolor=[("selected", p.accent_soft), ("active", p.hover)],
            darkcolor=[("selected", p.accent_soft), ("active", p.hover)],
            foreground=[("disabled", p.faint), ("selected", p.accent)],
        )
    style.configure("Underline.Toolbutton", font=fonts.underline)

    style.configure(
        "TEntry",
        padding=(6, 4),
        fieldbackground=p.surface,
        foreground=p.text,
        bordercolor=p.border,
        lightcolor=p.surface,
        darkcolor=p.surface,
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", p.accent)],
        lightcolor=[("focus", p.surface)],
        fieldbackground=[("disabled", p.window)],
    )
    style.configure("Error.TEntry", bordercolor=p.danger)
    style.map("Error.TEntry", bordercolor=[("focus", p.danger)])

    for prefix, background in (("", p.window), ("Surface.", p.surface)):
        for widget in ("TCheckbutton", "TRadiobutton"):
            style.configure(
                f"{prefix}{widget}",
                background=background,
                foreground=p.text,
                indicatorbackground=p.surface,
                indicatorforeground=p.accent,
                upperbordercolor=p.border,
                lowerbordercolor=p.border,
                focuscolor=background,
            )
            style.map(
                f"{prefix}{widget}",
                background=[("active", background)],
                indicatorbackground=[("pressed", p.hover), ("selected", p.surface)],
            )

    style.configure(
        "Treeview",
        background=p.surface,
        fieldbackground=p.surface,
        foreground=p.text,
        bordercolor=p.surface,
        lightcolor=p.surface,
        darkcolor=p.surface,
        rowheight=fonts.ui.metrics("linespace") + 10,
        indent=0,
    )
    style.map(
        "Treeview",
        background=[("selected", p.accent_soft)],
        foreground=[("selected", p.text)],
    )
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no border

    for orient, sticky in (("Horizontal", "we"), ("Vertical", "ns")):
        name = f"Slim.{orient}.TScrollbar"
        style.layout(
            name,
            [
                (
                    f"{orient}.Scrollbar.trough",
                    {
                        "sticky": sticky,
                        "children": [(f"{orient}.Scrollbar.thumb", {"sticky": "nswe"})],
                    },
                )
            ],
        )
        style.configure(
            name,
            troughcolor=p.view,
            background=p.thumb,
            bordercolor=p.view,
            lightcolor=p.thumb,
            darkcolor=p.thumb,
            gripcount=0,
            arrowsize=9,
        )
        style.map(
            name,
            background=[("pressed", p.thumb_active), ("active", p.thumb_active)],
            lightcolor=[("pressed", p.thumb_active), ("active", p.thumb_active)],
            darkcolor=[("pressed", p.thumb_active), ("active", p.thumb_active)],
        )
        style.configure(f"Panel.{name}", troughcolor=p.surface, bordercolor=p.surface)

    style.configure("TPanedwindow", background=p.border)
    style.configure("Sash", sashthickness=5, gripcount=0, background=p.window, bordercolor=p.border)
    style.configure("TSeparator", background=p.border)
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=p.border,
        background=p.accent,
        bordercolor=p.border,
        lightcolor=p.accent,
        darkcolor=p.accent,
        thickness=4,
    )

    # Classic Tk widgets (menus, dialogs from tkinter.messagebox on X11).
    root.option_add("*Menu.background", p.surface)
    root.option_add("*Menu.foreground", p.text)
    root.option_add("*Menu.activeBackground", p.accent_soft)
    root.option_add("*Menu.activeForeground", p.text)
    root.option_add("*Menu.disabledForeground", p.faint)
    root.option_add("*Menu.selectColor", p.accent)
    root.option_add("*Menu.relief", "flat")
    root.option_add("*Menu.borderWidth", 1)
    root.option_add("*Menu.activeBorderWidth", 0)
    root.configure(background=p.window)


def style_menu(menu: tk.Menu, palette: Palette) -> None:
    """Recolour an existing menu tree (the option database only affects new menus)."""
    p = palette
    menu.configure(
        background=p.surface,
        foreground=p.text,
        activebackground=p.accent_soft,
        activeforeground=p.text,
        disabledforeground=p.faint,
        selectcolor=p.accent,
    )
    end = menu.index("end")
    for index in range(0 if end is None else end + 1):
        if menu.type(index) == "cascade":
            submenu = menu.nametowidget(menu.entrycget(index, "menu"))
            style_menu(submenu, palette)
