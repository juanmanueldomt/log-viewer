"""The main window: menus, toolbar, log view, side panel and status bar."""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from collections.abc import Callable
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from types import TracebackType

from .. import APP_NAME, __version__
from ..core.export import marks_report
from ..core.lineset import LineSet
from ..core.marks import Mark
from ..core.query import Query, QueryError
from ..core.rules import Rule, RuleAction, next_color
from ..core.session import Change, LogSession
from ..core.settings import FONT_SIZES, FileState, Settings, SettingsStore
from ..core.text import UnsupportedEncodingError
from .dialogs import GoToLineDialog, MarkDialog, RuleDialog, ShortcutsDialog
from .executor import TkExecutor
from .logview import ContextInfo, LogView
from .searchbar import SearchBar
from .sidepanel import SidePanel
from .theme import LIGHT, PALETTES, Fonts, apply_theme, style_menu
from .widgets import Tooltip, app_icon

log = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"
MOD = "Command" if IS_MAC else "Control"
ALT = "Option" if IS_MAC else "Alt"
POLL_MS = 500
SAVE_DELAY_MS = 1500
MESSAGE_MS = 5000
DEFAULT_FONT_SIZE = Settings().font_size
FILE_TYPES = [("Log files", "*.log *.txt *.out *.err *.trace"), ("All files", "*")]


def keys(combo: str) -> str:
    """Display form of a shortcut written like ``Mod+Shift+F``."""
    return combo.replace("Mod+", "Cmd+" if IS_MAC else "Ctrl+").replace(
        "Alt+", "Option+" if IS_MAC else "Alt+"
    )


def accelerator(combo: str) -> str:
    """Menu accelerator text (Tk draws the macOS symbols itself)."""
    if IS_MAC:
        return (
            combo.replace("Mod+", "Command-").replace("Shift+", "Shift-").replace("Alt+", "Option-")
        )
    return keys(combo)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def describe_error(error: BaseException) -> str:
    if isinstance(error, FileNotFoundError):
        return "The file does not exist."
    if isinstance(error, IsADirectoryError):
        return "This is a folder, not a file."
    if isinstance(error, PermissionError):
        return "You do not have permission to read this file."
    if isinstance(error, UnsupportedEncodingError):
        return str(error)
    return str(error) or type(error).__name__


def shorten(text: str, limit: int = 32) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\N{HORIZONTAL ELLIPSIS}"


SHORTCUTS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "File",
        (
            ("Mod+O", "Open a file"),
            ("F5", "Reload from disk"),
            ("Mod+T", "Follow the file as it grows (tail)"),
            ("Mod+W", "Close the file"),
        ),
    ),
    (
        "Search",
        (
            ("Mod+F  or  /", "Search"),
            ("Enter  F3  n", "Next match"),
            ("Shift+Enter  Shift+F3  N", "Previous match"),
            ("Mod+Shift+F", "Show only the matching lines"),
            ("Alt+C  Alt+W  Alt+R", "Match case, whole word, regex"),
            ("Esc", "Clear the search"),
        ),
    ),
    (
        "Marks",
        (
            ("Mod+M  or  m", "Mark the line or the selected lines"),
            ("Click a line number", "Mark or unmark a line"),
            ("Shift+click a number", "Extend a mark into a section"),
            ("F2  Shift+F2", "Next or previous mark"),
        ),
    ),
    (
        "Moving around",
        (
            ("Mod+G", "Go to line"),
            ("Up  Down  PgUp  PgDn", "Move the current line"),
            ("Home  End", "First or last line (End resumes following)"),
            ("Shift+Up  Shift+Down", "Select lines"),
            ("Mod+C", "Copy the selection or the current line"),
            ("Mod+A", "Select everything"),
        ),
    ),
    (
        "View",
        (
            ("Mod+B", "Show or hide the side panel"),
            ("Alt+Z", "Wrap long lines"),
            ("Mod+Plus  Mod+Minus  Mod+0", "Zoom in, out, reset"),
            ("F1", "This reference"),
        ),
    ),
)


class MainWindow:
    """Owns the widgets and turns user actions into session operations."""

    def __init__(self, root: tk.Tk, store: SettingsStore, settings: Settings) -> None:
        self.root = root
        self.store = store
        self.settings = settings
        self.palette = PALETTES.get(settings.theme, LIGHT)
        self.fonts = Fonts.create(root, settings.font_size)
        apply_theme(root, self.palette, self.fonts)
        Tooltip.palette, Tooltip.font = self.palette, self.fonts.small

        self.executor = TkExecutor(root)
        self.session = LogSession(self.executor, settings.rules, hide_empty=settings.hide_empty)

        self._follow = tk.BooleanVar(root, False)
        self._filter = tk.BooleanVar(root, False)
        self._wrap = tk.BooleanVar(root, settings.wrap)
        self._hide_empty = tk.BooleanVar(root, settings.hide_empty)
        self._panel = tk.BooleanVar(root, settings.side_panel)
        self._theme = tk.StringVar(root, self.palette.name)
        self._pending = Change.NONE
        self._apply_id: str | None = None
        self._closed = False
        self._labels: dict[str, tuple[str, str]] = {}
        self._progress_shown = False
        self._poll_id: str | None = None
        self._save_id: str | None = None
        self._state_id: str | None = None
        self._message_id: str | None = None
        self._message = ""
        self._goto_line: int | None = None  # line to show once loaded far enough
        self._find_pending: bool | None = None  # direction of a find waiting for results
        self._reveal_from: int | None = None  # jump to the first match after this line

        root.title(APP_NAME)
        root.minsize(720, 380)
        root.geometry(settings.geometry or "1200x760")
        self._icon = app_icon(root, self.palette.accent)
        root.iconphoto(True, self._icon)
        root.report_callback_exception = self._report_error
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_status()
        self._bind_shortcuts()
        self._unsubscribe = self.session.subscribe(self._on_session_change)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        if IS_MAC:
            root.createcommand("::tk::mac::Quit", self.quit)
        self._rebuild_recent()
        self._update_welcome()
        self._update_status()
        self._sash_restored = False
        self.paned.bind("<Configure>", self._on_paned_configure, add="+")

    # -- construction ---------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root, tearoff=False)
        self.root.configure(menu=menubar)
        self.menubar = menubar

        def menu(label: str) -> tk.Menu:
            submenu = tk.Menu(menubar, tearoff=False)
            menubar.add_cascade(label=label, menu=submenu)
            return submenu

        file = menu("File")
        file.add_command(label="Open…", accelerator=accelerator("Mod+O"), command=self.open_dialog)
        self.recent_menu = tk.Menu(file, tearoff=False)
        file.add_cascade(label="Open Recent", menu=self.recent_menu)
        file.add_command(label="Reload", accelerator="F5", command=self.reload)
        file.add_checkbutton(
            label="Follow (Tail)",
            accelerator=accelerator("Mod+T"),
            variable=self._follow,
            command=self._follow_changed,
        )
        file.add_separator()
        file.add_command(label="Export Marks…", command=self.export_marks)
        file.add_separator()
        file.add_command(label="Close", accelerator=accelerator("Mod+W"), command=self.close_file)
        file.add_command(label="Quit", accelerator=accelerator("Mod+Q"), command=self.quit)

        edit = menu("Edit")
        edit.add_command(label="Copy", accelerator=accelerator("Mod+C"), command=self.copy)
        edit.add_command(
            label="Select All", accelerator=accelerator("Mod+A"), command=self.select_all
        )
        edit.add_separator()
        edit.add_command(
            label="Go to Line…", accelerator=accelerator("Mod+G"), command=self.go_to_line
        )

        search = menu("Search")
        search.add_command(
            label="Find", accelerator=accelerator("Mod+F"), command=self.focus_search
        )
        search.add_command(
            label="Find Next", accelerator="F3", command=lambda: self.find_next(False)
        )
        search.add_command(
            label="Find Previous",
            accelerator=accelerator("Shift+F3"),
            command=lambda: self.find_next(True),
        )
        search.add_separator()
        search.add_checkbutton(
            label="Show Only Matching Lines",
            accelerator=accelerator("Mod+Shift+F"),
            variable=self._filter,
            command=self._filter_menu_toggled,
        )
        search.add_command(
            label="Hide Lines Containing\N{HORIZONTAL ELLIPSIS}",
            command=lambda: self.add_rule_dialog(action=RuleAction.HIDE),
        )
        search.add_checkbutton(
            label="Hide Empty Lines", variable=self._hide_empty, command=self._hide_empty_changed
        )
        search.add_separator()
        search.add_command(label="Add Highlight Rule…", command=self.add_rule_dialog)

        marks = menu("Marks")
        marks.add_command(
            label="Mark Line or Selection",
            accelerator=accelerator("Mod+M"),
            command=self.toggle_mark,
        )
        marks.add_command(
            label="Next Mark", accelerator="F2", command=lambda: self.next_mark(False)
        )
        marks.add_command(
            label="Previous Mark",
            accelerator=accelerator("Shift+F2"),
            command=lambda: self.next_mark(True),
        )
        marks.add_separator()
        marks.add_command(label="Export Marks…", command=self.export_marks)
        marks.add_command(label="Clear All Marks", command=self.clear_marks)

        view = menu("View")
        view.add_checkbutton(
            label="Side Panel",
            accelerator=accelerator("Mod+B"),
            variable=self._panel,
            command=self._panel_changed,
        )
        view.add_checkbutton(
            label="Word Wrap",
            accelerator=accelerator("Alt+Z"),
            variable=self._wrap,
            command=self._wrap_changed,
        )
        view.add_separator()
        view.add_command(
            label="Zoom In", accelerator=accelerator("Mod++"), command=lambda: self.zoom(1)
        )
        view.add_command(
            label="Zoom Out", accelerator=accelerator("Mod+-"), command=lambda: self.zoom(-1)
        )
        view.add_command(
            label="Reset Zoom", accelerator=accelerator("Mod+0"), command=lambda: self.zoom(0)
        )
        view.add_separator()
        theme = tk.Menu(view, tearoff=False)
        view.add_cascade(label="Theme", menu=theme)
        for palette in PALETTES.values():
            theme.add_radiobutton(
                label=palette.name.title(),
                value=palette.name,
                variable=self._theme,
                command=partial(self.set_theme, palette.name),
            )

        help_menu = menu("Help")
        help_menu.add_command(
            label="Keyboard Shortcuts", accelerator="F1", command=self.show_shortcuts
        )
        help_menu.add_command(label=f"About {APP_NAME}", command=self.show_about)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(8, 6, 8, 6))
        bar.grid(row=0, column=0, sticky="ew")
        open_button = ttk.Button(
            bar, text="Open…", style="Toolbutton", command=self.open_dialog, takefocus=False
        )
        open_button.pack(side="left")
        Tooltip(open_button, f"Open a log file ({keys('Mod+O')})")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=(6, 10), pady=3)

        panel = ttk.Checkbutton(
            bar,
            text="☰",
            variable=self._panel,
            style="Toolbutton",
            command=self._panel_changed,
            takefocus=False,
            width=2,
        )
        panel.pack(side="right")
        Tooltip(panel, f"Highlights and marks panel ({keys('Mod+B')})")
        follow = ttk.Checkbutton(
            bar,
            text="Follow",
            variable=self._follow,
            style="Toolbutton",
            command=self._follow_changed,
            takefocus=False,
        )
        follow.pack(side="right", padx=(10, 4))
        Tooltip(follow, f"Show new lines as they are written, like tail -f ({keys('Mod+T')})")

        self.search = SearchBar(bar, self.settings, self.fonts, self.palette, self._filter)
        self.search.pack(side="left", fill="x", expand=True)
        self.search.on_change = self._on_query
        self.search.on_next = self.find_next
        self.search.on_filter = self._filter_changed
        self.search.on_escape = self.view_focus
        ttk.Separator(self.root).grid(row=1, column=0, sticky="ew")

    def _build_body(self) -> None:
        self.paned = ttk.Panedwindow(self.root, orient="horizontal")
        self.paned.grid(row=2, column=0, sticky="nsew")
        container = ttk.Frame(self.paned, style="View.TFrame")
        self.view = LogView(container, self.session, self.palette, self.fonts)
        self.view.pack(fill="both", expand=True)
        self.view.set_wrap(self.settings.wrap)
        self.view.on_view_changed = self._update_status
        self.view.on_message = self.show_message
        self.view.on_toggle_mark = self._on_gutter_click
        self.view.on_context_menu = self._on_context_menu
        self.view.on_zoom = self.zoom
        for sequence, action in (
            ("<slash>", self.focus_search),
            ("<n>", lambda: self.find_next(False)),
            ("<N>", lambda: self.find_next(True)),
            ("<m>", self.toggle_mark),
        ):
            self.view.bind_key(sequence, action)
        self.welcome = ttk.Frame(container, style="View.TFrame")
        self._build_welcome(self.welcome)

        self.side = SidePanel(self.paned, self.session, self.palette, self.fonts)
        self.paned.add(container, weight=1)
        if self.settings.side_panel:
            self.paned.add(self.side, weight=0)
        rules = self.side.rules
        rules.on_add = self.add_rule_dialog
        rules.on_edit = self.edit_rule
        rules.on_remove = self.remove_rule
        rules.on_toggle = self.toggle_rule
        rules.on_move = self.move_rule
        rules.on_navigate = self.navigate_rule
        marks = self.side.marks
        marks.on_go = self._go_to_mark
        marks.on_edit = self.edit_mark
        marks.on_remove = self.session.remove_mark
        marks.on_export = self.export_marks
        marks.on_clear = self.clear_marks
        marks.on_navigate = self.next_mark
        rules.refresh()

    def _build_welcome(self, frame: ttk.Frame) -> None:
        inner = ttk.Frame(frame, style="View.TFrame")
        inner.place(relx=0.5, rely=0.42, anchor="center")
        ttk.Label(inner, text=APP_NAME, style="View.Title.TLabel").pack()
        ttk.Label(
            inner,
            text="Search, highlight, mark and follow log files of any size.",
            style="View.Muted.TLabel",
        ).pack(pady=(4, 18))
        ttk.Button(
            inner, text="Open a log file…", style="Accent.TButton", command=self.open_dialog
        ).pack()
        self._recent_frame = ttk.Frame(inner, style="View.TFrame")
        self._recent_frame.pack(pady=(24, 0), fill="x")
        tips = "     ".join(
            f"{keys(combo)} {label}"
            for combo, label in (
                ("Mod+O", "open"),
                ("Mod+F", "search"),
                ("Mod+T", "follow"),
                ("F1", "all shortcuts"),
            )
        )
        ttk.Label(inner, text=tips, style="View.Faint.TLabel").pack(pady=(26, 0))

    def _build_status(self) -> None:
        ttk.Separator(self.root).grid(row=3, column=0, sticky="ew")
        bar = ttk.Frame(self.root, padding=(6, 3, 6, 4))
        bar.grid(row=4, column=0, sticky="ew")
        self.position_label = ttk.Label(bar, style="Status.TLabel")
        self.position_label.pack(side="left")
        self.filter_label = ttk.Label(bar, style="Status.TLabel")
        self.filter_label.pack(side="left")
        self.message_label = ttk.Label(bar, style="Status.TLabel")
        self.message_label.pack(side="left", padx=(8, 0))
        self.follow_label = ttk.Label(bar, style="Status.TLabel", cursor="hand2")
        self.follow_label.pack(side="right")
        self.follow_label.bind("<Button-1>", lambda _e: self._resume_follow())
        self.file_label = ttk.Label(bar, style="Status.TLabel")
        self.file_label.pack(side="right")
        self.activity_label = ttk.Label(bar, style="Status.TLabel")
        self.activity_label.pack(side="right")
        self.progress = ttk.Progressbar(bar, length=80, maximum=1.0, mode="determinate")

    def _bind_shortcuts(self) -> None:
        bindings: list[tuple[str, Callable[[], object]]] = [
            (f"<{MOD}-o>", self.open_dialog),
            (f"<{MOD}-r>", self.reload),
            ("<F5>", self.reload),
            (f"<{MOD}-w>", self.close_file),
            (f"<{MOD}-q>", self.quit),
            (f"<{MOD}-f>", self.focus_search),
            ("<F3>", lambda: self.find_next(False)),
            ("<Shift-F3>", lambda: self.find_next(True)),
            (f"<{MOD}-F>", self.toggle_filter),  # Mod+Shift+F
            (f"<{MOD}-g>", self.go_to_line),
            (f"<{MOD}-m>", self.toggle_mark),
            ("<F2>", lambda: self.next_mark(False)),
            ("<Shift-F2>", lambda: self.next_mark(True)),
            (f"<{MOD}-t>", self.toggle_follow),
            (f"<{MOD}-b>", self.toggle_panel),
            (f"<{ALT}-z>", self.toggle_wrap),
            (f"<{MOD}-plus>", lambda: self.zoom(1)),
            (f"<{MOD}-equal>", lambda: self.zoom(1)),
            (f"<{MOD}-KP_Add>", lambda: self.zoom(1)),
            (f"<{MOD}-minus>", lambda: self.zoom(-1)),
            (f"<{MOD}-KP_Subtract>", lambda: self.zoom(-1)),
            (f"<{MOD}-0>", lambda: self.zoom(0)),
            ("<F1>", self.show_shortcuts),
        ]
        for sequence, action in bindings:

            def handler(_event: tk.Event[tk.Misc], action: Callable[[], object] = action) -> str:
                action()
                return "break"

            self.root.bind(sequence, handler)
            # Also on the search field, ahead of the Entry class bindings (on X11
            # Ctrl+T there would otherwise transpose characters, Ctrl+K cut, ...).
            self.search.entry.bind(sequence, handler)

    # -- files ----------------------------------------------------------------

    def open_dialog(self) -> None:
        recent = self.settings.recent_files
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Open log file",
            filetypes=FILE_TYPES,
            initialdir=str(Path(recent[0]).parent) if recent else None,
        )
        if path:
            self.open_path(path)

    def open_path(
        self, path: str | Path, *, line: int | None = None, follow: bool | None = None
    ) -> bool:
        """Open *path*; start at *line* (0-based) or where the file was left last time."""
        self._save_file_state()
        try:
            file = self.session.open(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Cannot open file", f"{path}\n\n{describe_error(exc)}", parent=self.root
            )
            self.settings.forget_file(str(path))
            self._rebuild_recent()
            return False
        self.view.reset()
        self._find_pending = self._reveal_from = None
        self.settings.remember_file(str(file.path))
        self._rebuild_recent()
        self._schedule_save()
        state = self.store.load_file_state(file.path)
        restored = None
        if (
            state is not None
            and state.fingerprint is not None
            and state.fingerprint.matches(file.path)
        ):
            self.session.set_marks(state.marks)
            restored = state.line
        if follow is not None:
            self._follow.set(follow)
        if self._follow.get():
            self.view.scroll_to_end()
            self._schedule_poll()
        else:
            self._goto_line = line if line is not None else restored
        self.view.focus()
        return True

    def reload(self) -> None:
        if self.session.file is None:
            return
        line = self._reference_line()
        if self.session.reload() and not self._follow.get():
            self._goto_line = line

    def close_file(self) -> None:
        if self.session.file is None:
            return
        self._save_file_state()
        self.session.close()
        self.view.reset()

    def quit(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._save_file_state()
        self.settings.geometry = self.root.wm_geometry()
        self.settings.side_panel_width = self._panel_width()
        self.settings.rules = self.session.rules
        self.store.save(self.settings)
        self._unsubscribe()
        self.executor.shutdown()
        self.session.close()
        for after_id in (
            self._apply_id,
            self._poll_id,
            self._save_id,
            self._state_id,
            self._message_id,
        ):
            if after_id is not None:
                self.root.after_cancel(after_id)
        self.root.destroy()

    def _save_file_state(self) -> None:
        file = self.session.file
        if file is None:
            return
        line = self._reference_line() or 0
        marks = list(self.session.marks)
        state = FileState(file.fingerprint, marks, line) if marks or line else None
        self.store.save_file_state(file.path, state)

    def _rebuild_recent(self) -> None:
        menu = self.recent_menu
        menu.delete(0, "end")
        recent = self.settings.recent_files
        for path in recent:
            menu.add_command(label=path, command=partial(self.open_path, path))
        if recent:
            menu.add_separator()
            menu.add_command(label="Clear Recent", command=self._clear_recent)
        else:
            menu.add_command(label="No recent files", state="disabled")
        self._rebuild_welcome_recent()

    def _open_recent(self, path: str, _event: object = None) -> None:
        self.open_path(path)

    def _clear_recent(self) -> None:
        self.settings.recent_files.clear()
        self._rebuild_recent()
        self._schedule_save()

    def _rebuild_welcome_recent(self) -> None:
        frame = self._recent_frame
        for child in frame.winfo_children():
            child.destroy()
        recent = self.settings.recent_files[:6]
        if not recent:
            return
        ttk.Label(frame, text="Recent", style="View.Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        for row, path in enumerate(recent, start=1):
            name = ttk.Label(frame, text=Path(path).name, style="View.Link.TLabel", cursor="hand2")
            name.grid(row=row, column=0, sticky="w", pady=1)
            name.bind("<Button-1>", partial(self._open_recent, path))
            folder = shorten(str(Path(path).parent), 60)
            ttk.Label(frame, text=folder, style="View.Faint.TLabel").grid(
                row=row, column=1, sticky="w", padx=(12, 0)
            )

    # -- search & filter ------------------------------------------------------

    def focus_search(self) -> None:
        self.search.focus_entry()

    def view_focus(self) -> None:
        self.view.focus()

    def _on_query(self, query: Query | None) -> None:
        filtering = self.session.filter_to_search
        if filtering:
            self.view.keep_position()
        try:
            self.session.set_search(query)
        except QueryError as exc:
            self.search.set_status("Invalid pattern", error=True)
            self.show_message(str(exc), error=True)
            return
        self._find_pending = None
        # As results arrive, jump to the first match from the current line on (inclusive).
        current = self.view.current_line
        origin = current - 1 if current is not None else self._reference_line(before=True)
        self._reveal_from = None if query is None or filtering else origin
        self._update_search_status()
        self.view.refresh()

    def find_next(self, backwards: bool = False) -> None:
        scan = self.session.search_scan
        if scan is None:
            self.focus_search()
            return
        self._reveal_from = None
        origin = self._reference_line(before=not backwards)
        origin = 0 if origin is None else origin
        hit = self.session.find(scan.lines, origin, backwards=backwards)
        if hit is None or (hit.wrapped and not scan.complete):
            if not scan.complete:  # results may still come: try again when they do
                self._find_pending = backwards
                self.show_message("Searching\N{HORIZONTAL ELLIPSIS}")
            else:
                self.show_message("No matches")
            return
        self._find_pending = None
        if hit.wrapped:
            self.show_message(
                "Reached the top, continued from the bottom"
                if backwards
                else "Reached the end, continued from the top"
            )
        self._show_hit(hit.line)

    def _show_hit(self, line: int) -> None:
        self.view.go_to_line(line)
        self._update_search_status()

    def _continue_search(self) -> None:
        """Act on results that were not available yet when asked for."""
        scan = self.session.search_scan
        if scan is None:
            return
        if self._find_pending is not None:
            backwards, self._find_pending = self._find_pending, None
            self.find_next(backwards)
        elif self._reveal_from is not None:
            line = self._first_visible(scan.lines, self._reveal_from)
            if line is None and scan.complete and len(scan.lines):
                line = self._first_visible(scan.lines, -1)
            if line is not None or scan.complete:
                self._reveal_from = None
            if line is not None:
                self._show_hit(line)

    def _first_visible(self, lines: LineSet, after: int) -> int | None:
        line = lines.next_after(after)
        while line is not None and not self.session.is_visible(line):
            line = lines.next_after(line)
        return line

    def _update_search_status(self) -> None:
        scan = self.session.search_scan
        if scan is None:
            if self.session.search_query is None:
                self.search.set_status("")
            return
        if scan.error:
            self.search.set_status("Search failed", error=True)
            return
        count = len(scan.lines)
        line = self.view.current_line
        if not scan.complete:
            text = (
                f"{count:,}\N{HORIZONTAL ELLIPSIS}" if count else "Searching\N{HORIZONTAL ELLIPSIS}"
            )
        elif count == 0:
            text = "No results"
        elif line is not None and line in scan.lines:
            text = f"{scan.lines.rank(line) + 1:,} of {count:,}"
        else:
            text = f"{count:,} result{'' if count == 1 else 's'}"
        self.search.set_status(text)

    def toggle_filter(self) -> None:
        self._filter.set(not self._filter.get())
        self._filter_changed(self._filter.get())

    def _filter_menu_toggled(self) -> None:
        self._filter_changed(self._filter.get())

    def _filter_changed(self, enabled: bool) -> None:
        self.view.keep_position()
        self.session.set_filter_to_search(enabled)
        if enabled and self.session.search_query is None:
            self.focus_search()
            self.show_message("Type what the lines must contain")

    def _filter_by(self, text: str) -> None:
        self.search.set_query(text)
        if not self._filter.get():
            self.toggle_filter()

    def _hide_empty_changed(self) -> None:
        self.view.keep_position()
        self.session.set_hide_empty(self._hide_empty.get())
        self.settings.hide_empty = self._hide_empty.get()
        self._schedule_save()

    # -- rules ----------------------------------------------------------------

    def _set_rules(self, rules: list[Rule], select: int | None = None) -> None:
        if [r for r in self.session.rules if r.hides] != [r for r in rules if r.hides]:
            self.view.keep_position()
        self.session.set_rules(rules)
        self.settings.rules = list(rules)
        self._schedule_save()
        self.side.rules.refresh()
        if select is not None:
            self.side.rules.select(select)

    def add_rule_dialog(self, text: str = "", action: RuleAction = RuleAction.LINE) -> None:
        rules = self.session.rules
        color = next_color(rules)
        draft = Rule(Query(text), color=color, action=action)
        rule = RuleDialog(self.root, self.palette, draft, color, title="New rule").show()
        if rule is not None:
            self._set_rules([*rules, rule], select=len(rules))
            self._show_panel("rules")

    def quick_rule(self, text: str, action: RuleAction) -> None:
        rules = self.session.rules
        self._set_rules(
            [*rules, Rule(Query(text), color=next_color(rules), action=action)], len(rules)
        )
        verb = "Hiding" if action is RuleAction.HIDE else "Highlighting"
        self.show_message(f"{verb} lines with “{shorten(text)}” (see Highlights)")

    def edit_rule(self, index: int) -> None:
        rules = self.session.rules
        rule = RuleDialog(self.root, self.palette, rules[index], rules[index].color).show()
        if rule is not None:
            rules[index] = rule
            self._set_rules(rules, select=index)

    def remove_rule(self, index: int) -> None:
        rules = self.session.rules
        del rules[index]
        self._set_rules(rules, select=min(index, len(rules) - 1) if rules else None)

    def toggle_rule(self, index: int) -> None:
        rules = self.session.rules
        rules[index] = rules[index].with_changes(enabled=not rules[index].enabled)
        self._set_rules(rules, select=index)

    def move_rule(self, index: int, delta: int) -> None:
        rules = self.session.rules
        target = index + delta
        if 0 <= target < len(rules):
            rules[index], rules[target] = rules[target], rules[index]
            self._set_rules(rules, select=target)

    def navigate_rule(self, index: int, backwards: bool) -> None:
        rule = self.session.rules[index]
        scan = self.session.rule_scan(rule)
        if scan is None:
            self.show_message(
                "Hidden lines cannot be visited" if rule.hides else "Enable the rule first"
            )
            return
        origin = self._reference_line(before=not backwards) or 0
        hit = self.session.find(scan.lines, origin, backwards=backwards)
        if hit is None:
            self.show_message(
                "No matching lines" if scan.complete else "Still counting\N{HORIZONTAL ELLIPSIS}"
            )
            return
        self.view.go_to_line(hit.line)

    # -- marks ----------------------------------------------------------------

    def toggle_mark(self) -> None:
        lines = self.view.selected_lines()
        if lines is None:
            return
        if self.view.has_selection():
            self._mark_lines(*lines)
        else:
            self.session.toggle_mark(lines[0])

    def _mark_lines(self, first: int, last: int) -> None:
        self.session.add_mark(first, last)
        self.view.clear_selection()

    def _on_gutter_click(self, line: int, extend: bool) -> None:
        anchor = self.view.current_line
        if extend and anchor is not None and anchor != line:
            mark = self.session.marks.at(anchor)
            if mark is not None:
                self.session.edit_mark(mark, first=min(mark.first, line), last=max(mark.last, line))
            else:
                self.session.add_mark(min(anchor, line), max(anchor, line))
        else:
            self.session.toggle_mark(line)

    def next_mark(self, backwards: bool = False) -> None:
        marks = list(self.session.marks)
        if not marks:
            self.show_message("No marks yet: click a line number to mark a line")
            return
        current = self._reference_line(before=not backwards) or 0
        store = self.session.marks
        mark = store.prev_before(current) if backwards else store.next_after(current)
        if mark is None:
            mark = marks[-1] if backwards else marks[0]
            self.show_message("Wrapped around")
        self._go_to_mark(mark)

    def _go_to_mark(self, mark: Mark) -> None:
        self.view.go_to_line(mark.first)
        self.side.marks.select(mark)
        self.view.focus()

    def edit_mark(self, mark: Mark) -> None:
        result = MarkDialog(self.root, self.palette, mark).show()
        if result is not None:
            label, color = result
            self.session.edit_mark(mark, label=label, color=color)

    def clear_marks(self) -> None:
        count = len(self.session.marks)
        if count and messagebox.askyesno(
            "Clear marks", f"Remove all {count} marks from this file?", parent=self.root
        ):
            self.session.set_marks([])

    def export_marks(self) -> None:
        file = self.session.file
        if file is None or not self.session.marks:
            self.show_message("There are no marks to export")
            return
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export marks",
            defaultextension=".md",
            initialfile=f"{file.path.stem}-marks.md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*")],
        )
        if not target:
            return
        try:
            Path(target).write_text(marks_report(file, self.session.marks), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Export failed", describe_error(exc), parent=self.root)
            return
        self.show_message(f"Exported {len(self.session.marks)} marks to {Path(target).name}")

    # -- view -----------------------------------------------------------------

    def copy(self) -> None:
        widget = self.root.focus_get()
        if isinstance(widget, ttk.Entry):
            widget.event_generate("<<Copy>>")
        else:
            self.view.copy()

    def select_all(self) -> None:
        widget = self.root.focus_get()
        if isinstance(widget, ttk.Entry):
            widget.select_range(0, "end")
        else:
            self.view.select_all()

    def go_to_line(self) -> None:
        count = self.session.line_count
        if not count:
            return
        current = self._reference_line() or 0
        line = GoToLineDialog(self.root, self.palette, count, current).show()
        if line is None:
            return
        if not self.session.is_visible(line):
            self.show_message(f"Line {line + 1:,} is hidden by a filter; showing the next line")
        self.view.go_to_line(line)
        self.view.focus()

    def toggle_follow(self) -> None:
        self._follow.set(not self._follow.get())
        self._follow_changed()

    def _follow_changed(self) -> None:
        if self._follow.get():
            self.view.scroll_to_end()
            self._schedule_poll()
        elif self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None
        self._update_status()

    def _resume_follow(self) -> None:
        if self._follow.get():
            self.view.scroll_to_end()

    def _schedule_poll(self) -> None:
        if self._poll_id is None:
            self._poll_id = self.root.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        self._poll_id = None
        if not self._follow.get():
            return
        if self.session.file is not None:
            self.session.poll()
        self._schedule_poll()

    def toggle_panel(self) -> None:
        self._panel.set(not self._panel.get())
        self._panel_changed()

    def _show_panel(self, page: str) -> None:
        if not self._panel.get():
            self.toggle_panel()
        self.side.show(page)

    def _panel_changed(self) -> None:
        showing = self._panel_shown()
        if self._panel.get() and not showing:
            self.paned.add(self.side, weight=0)
            self.root.after_idle(self._restore_sash)
        elif not self._panel.get() and showing:
            self.settings.side_panel_width = self._panel_width()
            self.paned.forget(self.side)
        self.settings.side_panel = self._panel.get()
        self._schedule_save()

    def _panel_shown(self) -> bool:
        panes = self.paned.panes()  # type: ignore[no-untyped-call]
        return str(self.side) in {str(pane) for pane in panes}

    def _panel_width(self) -> int:
        if not self._panel_shown():
            return self.settings.side_panel_width
        sash = int(self.paned.sashpos(0))  # type: ignore[no-untyped-call]
        return max(180, self.paned.winfo_width() - sash)

    def _on_paned_configure(self, event: tk.Event[tk.Misc]) -> None:
        if not self._sash_restored and event.width > 1:  # first real size: now it can be split
            self._sash_restored = True
            self._restore_sash()

    def _restore_sash(self) -> None:
        if self._panel_shown():
            self.root.update_idletasks()
            width = self.paned.winfo_width()
            if width > 1:
                position = max(240, width - self.settings.side_panel_width)
                self.paned.sashpos(0, position)  # type: ignore[no-untyped-call]

    def toggle_wrap(self) -> None:
        self._wrap.set(not self._wrap.get())
        self._wrap_changed()

    def _wrap_changed(self) -> None:
        self.settings.wrap = self._wrap.get()
        self.view.set_wrap(self.settings.wrap)
        self._schedule_save()

    def zoom(self, step: int) -> None:
        size = DEFAULT_FONT_SIZE if step == 0 else self.settings.font_size + step
        if size in FONT_SIZES and size != self.settings.font_size:
            self.settings.font_size = size
            self.fonts.set_mono_size(size)
            self.view.font_changed()
            self.show_message(f"Font size {size}")
            self._schedule_save()

    def set_theme(self, name: str) -> None:
        self.palette = PALETTES[name]
        self._theme.set(name)
        self.settings.theme = name
        apply_theme(self.root, self.palette, self.fonts)
        style_menu(self.menubar, self.palette)
        Tooltip.palette = self.palette
        self.view.apply_palette(self.palette)
        self.side.apply_palette(self.palette)
        self.search.apply_palette(self.palette)
        self._schedule_save()

    def show_shortcuts(self) -> None:
        groups = [
            (title, [(keys(combo), text) for combo, text in entries])
            for title, entries in SHORTCUTS
        ]
        ShortcutsDialog(self.root, self.palette, self.fonts, groups).show()

    def show_about(self) -> None:
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} {__version__}\n\nA fast, minimal viewer for large log files.\n"
            "Search, highlight, mark and follow logs of any size.\n\n"
            "https://github.com/juanmanueldomt/log-viewer",
            parent=self.root,
        )

    # -- context menu ---------------------------------------------------------

    def _on_context_menu(self, event: tk.Event[tk.Misc], info: ContextInfo) -> None:
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(
            label="Copy" if self.view.has_selection() else "Copy Line",
            accelerator=accelerator("Mod+C"),
            command=self.view.copy,
        )
        if info.text:
            text, label = info.text, shorten(info.text)
            menu.add_separator()
            menu.add_command(
                label=f"Search for “{label}”", command=lambda: self.search.set_query(text)
            )
            menu.add_command(
                label=f"Highlight “{label}”",
                command=lambda: self.quick_rule(text, RuleAction.LINE),
            )
            menu.add_command(
                label=f"Show Only Lines with “{label}”", command=lambda: self._filter_by(text)
            )
            menu.add_command(
                label=f"Hide Lines with “{label}”",
                command=lambda: self.quick_rule(text, RuleAction.HIDE),
            )
        menu.add_separator()
        if info.lines is not None:
            first, last = info.lines
            menu.add_command(
                label=f"Mark Lines {first + 1:,}\N{EN DASH}{last + 1:,}",
                accelerator=accelerator("Mod+M"),
                command=lambda: self._mark_lines(first, last),
            )
        elif info.mark is not None:
            mark = info.mark
            menu.add_command(label="Edit Mark…", command=lambda: self.edit_mark(mark))
            menu.add_command(label="Remove Mark", command=lambda: self.session.remove_mark(mark))
        else:
            menu.add_command(
                label=f"Mark Line {info.line + 1:,}",
                accelerator=accelerator("Mod+M"),
                command=lambda: self.session.toggle_mark(info.line),
            )
        menu.add_command(
            label="Go to Line…", accelerator=accelerator("Mod+G"), command=self.go_to_line
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # -- session changes & status --------------------------------------------

    def _on_session_change(self, change: Change) -> None:
        self._pending |= change
        if self._apply_id is None:
            self._apply_id = self.root.after_idle(self._apply_changes)

    def _apply_changes(self) -> None:
        change, self._pending = self._pending, Change.NONE
        self._apply_id = None
        if Change.FILE in change:
            file = self.session.file
            self.root.title(f"{file.name} \N{EM DASH} {APP_NAME}" if file else APP_NAME)
            self._update_welcome()
            self.side.rules.refresh()
        if change & ~Change.STATUS:
            self.view.refresh(ruler=True)
        if change & (Change.FILE | Change.RULES):
            self.side.rules.refresh_counts()
        if change & (Change.FILE | Change.MARKS):
            self.side.marks.refresh()
            self.side.update_titles()
        if Change.MARKS in change and self.session.file is not None:
            self._schedule_state_save()
        if change & (Change.FILE | Change.SEARCH | Change.ROWS | Change.LINES):
            self._continue_search()
            self._update_search_status()
        self._check_goto()
        self._update_status()

    def _check_goto(self) -> None:
        line = self._goto_line
        if line is None:
            return
        count = self.session.line_count
        if count > line or not self.session.loading:
            self._goto_line = None
            if count:
                self.view.go_to_line(min(line, count - 1))

    def _update_welcome(self) -> None:
        if self.session.file is None:
            self.welcome.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.welcome.lift()
        else:
            self.welcome.place_forget()

    def _reference_line(self, *, before: bool = False) -> int | None:
        """The current line, else the first visible one (minus one with *before*)."""
        if self.view.current_line is not None:
            return self.view.current_line
        rows = self.session.rows
        if not len(rows):
            return None
        line = rows.line_at(min(self.view.top_row, len(rows) - 1))
        return line - 1 if before else line

    def _update_status(self) -> None:
        session, file = self.session, self.session.file
        position = filtered = details = follow = ""
        if file is not None:
            rows, count = session.rows, session.line_count
            line = self._reference_line()
            position = f"Ln {line + 1:,} of {count:,}" if line is not None else f"{count:,} lines"
            if rows.filtered:
                filtered = f"  \N{MIDDLE DOT}  {len(rows):,} shown"
            details = f"{file.encoding.upper()}  \N{MIDDLE DOT}  {human_size(file.disk_size)}"
        following = self.view.at_end
        if self._follow.get():
            follow = "\N{BLACK CIRCLE} Following" if following else "Paused \N{EM DASH} End resumes"
        self._set_label(self.position_label, position)
        self._set_label(self.filter_label, filtered)
        self._set_label(self.file_label, details)
        self._set_label(self.follow_label, follow, "Accent" if following else "")
        activities = session.activities()
        if activities:
            activity = activities[0]
            self._set_label(self.activity_label, f"{activity.label} {activity.progress:.0%}")
            self.progress.configure(value=activity.progress)
            if not self._progress_shown:
                self.progress.pack(side="right", padx=(0, 6), before=self.activity_label)
                self._progress_shown = True
        else:
            self._set_label(self.activity_label, "")
            if self._progress_shown:
                self.progress.pack_forget()
                self._progress_shown = False
        self._update_search_status()
        message = session.message or self._message
        self._set_label(self.message_label, message, "Error" if session.message else "")

    def _set_label(self, label: ttk.Label, text: str, variant: str = "") -> None:
        """Update a status label only if it changed: Tk calls are costly while scans run."""
        style = f"{variant}.Status.TLabel" if variant else "Status.TLabel"
        if self._labels.get(str(label)) != (text, style):
            self._labels[str(label)] = (text, style)
            label.configure(text=text, style=style)

    def show_message(self, text: str, *, error: bool = False) -> None:
        """A short-lived note in the status bar."""
        self._message = text
        self._set_label(self.message_label, text, "Error" if error else "")
        if self._message_id is not None:
            self.root.after_cancel(self._message_id)
        self._message_id = self.root.after(MESSAGE_MS, self._clear_message)

    def _clear_message(self) -> None:
        self._message_id = None
        self._message = ""
        self._update_status()

    def _schedule_save(self) -> None:
        if self._save_id is not None:
            self.root.after_cancel(self._save_id)
        self._save_id = self.root.after(SAVE_DELAY_MS, self._save_settings)

    def _save_settings(self) -> None:
        self._save_id = None
        self.store.save(self.settings)

    def _schedule_state_save(self) -> None:
        if self._state_id is not None:
            self.root.after_cancel(self._state_id)
        self._state_id = self.root.after(SAVE_DELAY_MS, self._save_state_now)

    def _save_state_now(self) -> None:
        self._state_id = None
        self._save_file_state()

    def _report_error(
        self, kind: type[BaseException], error: BaseException, trace: TracebackType | None
    ) -> None:
        log.error("Unexpected error", exc_info=(kind, error, trace))
        self.show_message(f"Unexpected error: {error}", error=True)
