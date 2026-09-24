"""End-to-end tests driving the real Tk interface with synthetic events.

They need a display; on headless Linux run them under ``xvfb-run``.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

try:
    import tkinter as tk
except ImportError:  # Python built without Tk
    pytest.skip("tkinter is not available", allow_module_level=True)

from jmlogviewer.core.rules import RuleAction
from jmlogviewer.core.settings import Settings, SettingsStore
from jmlogviewer.ui.logview import END, Position
from jmlogviewer.ui.main_window import MainWindow

pytestmark = pytest.mark.gui

LINES = [
    f"2026-01-01 10:00:{i % 60:02d} {'ERROR' if i % 10 == 3 else 'INFO'} request {i} done"
    for i in range(500)
]


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MainWindow]:
    monkeypatch.setenv("JMLOGVIEWER_CONFIG_DIR", str(tmp_path / "config"))
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # no display
        pytest.skip(f"no display: {exc}")
    store = SettingsStore()
    window = MainWindow(root, store, Settings(geometry="1100x700+0+0"))
    root.update()
    yield window
    with contextlib.suppress(tk.TclError):  # unless the test already closed it
        window.quit()


def settle(
    window: MainWindow, until: Callable[[], bool] | None = None, timeout: float = 10
) -> None:
    """Process events until background work is done (and *until* holds)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        window.root.update()
        idle = not window.session.activities()
        if idle and (until is None or until()):
            for _ in range(3):
                window.root.update()
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the UI to settle")


def open_log(window: MainWindow, tmp_path: Path, lines: list[str] = LINES) -> Path:
    path = tmp_path / "app.log"
    path.write_text("\n".join(lines) + "\n")
    assert window.open_path(path)
    settle(window, lambda: window.session.line_count == len(lines))
    return path


def text_widget(window: MainWindow) -> tk.Text:
    return window.view._text


def row_y(window: MainWindow, index: int) -> int:
    info = text_widget(window).dlineinfo(f"{index + 1}.0")
    assert info is not None
    return int(info[1] + info[3] // 2)


def press_key(widget: tk.Misc, keysym: str, state: int = 0) -> None:
    widget.focus_force()
    widget.update()
    widget.event_generate(f"<KeyPress-{keysym}>", when="now", state=state)
    widget.update()


def test_open_shows_lines_and_status(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    assert window.root.title().startswith("app.log")
    assert not window.welcome.winfo_ismapped()
    shown = text_widget(window).get("1.0", "1.end")
    assert shown == LINES[0]
    assert "of 500" in window.position_label.cget("text")
    counts = [window.side.rules.tree.set(iid, "count") for iid in ("0", "1")]
    assert counts[0] == "50"  # the default ERROR rule found every tenth line


def test_welcome_screen_until_a_file_is_open(window: MainWindow) -> None:
    assert window.welcome.winfo_ismapped()


def test_click_drag_selects_and_copies(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    text = text_widget(window)
    text.event_generate("<Button-1>", x=10, y=row_y(window, 1), when="now")
    text.event_generate("<B1-Motion>", x=200, y=row_y(window, 3), when="now")
    text.event_generate("<ButtonRelease-1>", x=200, y=row_y(window, 3), when="now")
    window.root.update()
    assert window.view.current_line == 1
    assert window.view.selected_lines() == (1, 3)
    window.view.copy()
    copied = window.root.clipboard_get()
    assert copied.startswith(LINES[1][:3])
    assert copied.count("\n") == 2


def test_copy_without_selection_copies_the_current_line(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    window.view.go_to_line(42)
    window.view.copy()
    assert window.root.clipboard_get() == LINES[42]


def test_keyboard_navigation(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    text = text_widget(window)
    window.view.set_current_line(0)
    press_key(text, "Down")
    press_key(text, "Down")
    assert window.view.current_line == 2
    press_key(text, "Next")
    assert window.view.current_line > 10
    press_key(text, "End")
    settle(window)
    assert window.view.current_line == 499
    assert window.view.at_end
    press_key(text, "Home")
    settle(window)
    assert window.view.current_line == 0
    assert window.view.top_row == 0


def test_mouse_wheel_scrolls(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    text = text_widget(window)
    for _ in range(4):
        text.event_generate("<Button-5>", x=20, y=20, when="now")
    settle(window)
    assert window.view.top_row == 12


def test_gutter_click_marks_and_shift_click_extends(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    gutter = window.view._gutter
    gutter.event_generate("<Button-1>", x=5, y=row_y(window, 4), when="now")
    settle(window)
    mark = window.session.marks.at(4)
    assert mark is not None
    gutter.event_generate("<Button-1>", x=5, y=row_y(window, 9), state=0x1, when="now")
    settle(window)
    assert (mark.first, mark.last) == (4, 9)
    assert window.side.marks.tree.get_children()
    gutter.event_generate("<Button-1>", x=5, y=row_y(window, 6), when="now")
    settle(window)
    assert not window.session.marks


def test_mark_selection_and_navigate(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    view = window.view
    view._anchor, view._head = Position(20, 0), Position(22, END)
    window.toggle_mark()
    view.go_to_line(100)
    window.session.add_mark(200, 200)
    view.go_to_line(0)
    window.next_mark()
    assert view.current_line == 20
    window.next_mark()
    assert view.current_line == 200
    window.next_mark(backwards=True)
    assert view.current_line == 20


def test_search_as_you_type_and_navigation(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    window.search.entry.insert(0, "request 13 ")
    settle(window, lambda: window.session.search_scan is not None)
    assert window.view.current_line == 13  # jumped to the first match
    assert window.search.counter.cget("text") == "1 of 1"
    window.search.set_query("ERROR")
    settle(window)
    window.view.go_to_line(0)
    window.find_next()
    assert window.view.current_line == 3
    window.find_next()
    assert window.view.current_line == 13
    window.find_next(backwards=True)
    assert window.view.current_line == 3
    assert window.search.counter.cget("text") == "1 of 50"


def test_search_keeps_a_matching_current_line(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    window.view.go_to_line(13)
    window.search.set_query("ERROR")
    settle(window)
    assert window.view.current_line == 13
    assert window.search.counter.cget("text") == "2 of 50"


def test_invalid_regex_is_reported(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    window.search.regex.set(True)
    window.search.set_query("(unclosed", regex=True)
    settle(window)
    assert window.search.counter.cget("text") == "Invalid pattern"
    assert str(window.search.entry.cget("style")) == "Error.TEntry"


def test_filter_shortcut_keeps_the_current_line(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    window.search.set_query("ERROR")
    settle(window)
    window.view.go_to_line(253)
    press_key(window.view._text, "F", state=0x4 | 0x1)  # Ctrl+Shift+F
    settle(window)
    assert window.session.rows.filtered
    assert len(window.session.rows) == 50
    assert "50 shown" in window.filter_label.cget("text")
    assert window.view.current_line == 253
    rows_on_screen = text_widget(window).get("1.0", "end").splitlines()
    assert all("ERROR" in row for row in rows_on_screen if row)
    window.toggle_filter()
    settle(window)
    assert not window.session.rows.filtered


def test_hide_empty_lines(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path, ["a", "", "b", "   ", "c"])
    window._hide_empty.set(True)
    window._hide_empty_changed()
    settle(window)
    assert text_widget(window).get("1.0", "end").split() == ["a", "b", "c"]


def test_quick_hide_rule(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    before = len(window.session.rules)
    window.quick_rule("request 7", RuleAction.HIDE)
    settle(window)
    assert len(window.session.rules) == before + 1
    hidden = 1 + 10  # "request 7" and "request 70" to "request 79"
    assert len(window.session.rows) == 500 - hidden
    window.remove_rule(before)
    settle(window)
    assert len(window.session.rows) == 500


def test_follow_shows_appended_lines(window: MainWindow, tmp_path: Path) -> None:
    path = open_log(window, tmp_path)
    window.toggle_follow()
    with path.open("a") as handle:
        handle.write("new line 1\nnew line 2\n")
    settle(window, lambda: window.session.line_count == 502, timeout=5)
    settle(window)
    assert window.view.at_end
    last_rows = text_widget(window).get("1.0", "end").strip().splitlines()[-2:]
    assert last_rows == ["new line 1", "new line 2"]
    assert "Following" in window.follow_label.cget("text")
    window.toggle_follow()


def test_zoom_wrap_theme_and_panel(window: MainWindow, tmp_path: Path) -> None:
    open_log(window, tmp_path)
    size = window.settings.font_size
    window.zoom(1)
    assert window.settings.font_size == size + 1
    window.zoom(0)
    assert window.settings.font_size == size
    window.toggle_wrap()
    assert text_widget(window).cget("wrap") == "word"
    window.set_theme("dark")
    settle(window)
    assert text_widget(window).cget("background") == "#16191D"
    window.toggle_panel()
    settle(window)
    assert not window._panel_shown()
    window.toggle_panel()
    settle(window)
    assert window._panel_shown()


def test_settings_and_marks_are_remembered(window: MainWindow, tmp_path: Path) -> None:
    path = open_log(window, tmp_path)
    window.session.add_mark(10, 12, label="boot")
    window.view.go_to_line(300)
    window.set_theme("dark")
    window.quit()

    root = tk.Tk()
    store = SettingsStore()
    settings = store.load()
    assert settings.theme == "dark"
    assert settings.recent_files == [str(path)]
    reopened = MainWindow(root, store, settings)
    try:
        reopened.open_path(path)
        settle(reopened, lambda: reopened.view.current_line == 300)
        assert [(m.first, m.last, m.label) for m in reopened.session.marks] == [(10, 12, "boot")]
    finally:
        reopened.quit()


def test_open_missing_file_shows_an_error(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[str] = []
    monkeypatch.setattr(
        "tkinter.messagebox.showerror", lambda title, message, **_: errors.append(message)
    )
    assert not window.open_path(tmp_path / "missing.log")
    assert errors
    assert "does not exist" in errors[0]


def test_shortcuts_dialog_opens_and_closes(window: MainWindow) -> None:
    from jmlogviewer.ui.dialogs import ShortcutsDialog

    opened: list[ShortcutsDialog] = []

    def close() -> None:
        dialogs = [w for w in window.root.winfo_children() if isinstance(w, ShortcutsDialog)]
        opened.extend(dialogs)
        for dialog in dialogs:
            dialog.ok()

    window.root.after(300, close)
    window.show_shortcuts()
    assert len(opened) == 1
