"""Command-line entry point: ``jmlogviewer [FILE] [--follow] [--line N]``."""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from . import APP_NAME, __version__

GIL_SWITCH_INTERVAL = 0.0005


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jmlogviewer",
        description="A fast, minimal viewer for large log files.",
    )
    parser.add_argument("file", nargs="?", type=Path, help="log file to open")
    parser.add_argument(
        "-f", "--follow", action="store_true", help="follow the file as it grows, like tail -f"
    )
    parser.add_argument("-n", "--line", type=int, metavar="N", help="start at line N")
    parser.add_argument("--debug", action="store_true", help="write diagnostics to stderr")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser.parse_args(argv)


def _enable_dpi_awareness() -> None:
    """Render crisply on high-DPI Windows displays instead of being scaled up blurrily."""
    if sys.platform == "win32":
        import ctypes

        with contextlib.suppress(AttributeError, OSError):  # not available before Windows 8.1
            ctypes.windll.shcore.SetProcessDpiAwareness(1)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        import tkinter as tk
    except ImportError:
        print(
            f"{APP_NAME} needs Tkinter. Install it with your Python distribution "
            "(e.g. 'sudo apt install python3-tk' on Debian/Ubuntu).",
            file=sys.stderr,
        )
        return 1

    from .core.settings import SettingsStore
    from .ui.main_window import MainWindow

    _enable_dpi_awareness()
    # Scanning runs on a worker thread. With CPython's default 5 ms switch
    # interval, every Tk call the UI makes while a scan runs can wait that long
    # for the GIL; a short interval keeps the interface responsive.
    sys.setswitchinterval(GIL_SWITCH_INTERVAL)
    store = SettingsStore()
    root = tk.Tk(className=APP_NAME)
    window = MainWindow(root, store, store.load())
    if args.file is not None:
        line = None if args.line is None else max(0, args.line - 1)
        root.after_idle(lambda: window.open_path(args.file, line=line, follow=args.follow))
    elif args.follow:
        window.toggle_follow()
    root.mainloop()
    return 0
