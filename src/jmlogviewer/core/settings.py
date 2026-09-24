"""User settings and per-file state, persisted as JSON in the user's config directory."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from .. import APP_NAME
from .logfile import Fingerprint
from .marks import Mark
from .rules import Rule, default_rules

log = logging.getLogger(__name__)

CONFIG_DIR_ENV = "JMLOGVIEWER_CONFIG_DIR"
SCHEMA_VERSION = 1
MAX_RECENT_FILES = 10
MAX_SEARCH_HISTORY = 30
MAX_FILE_STATES = 200
THEMES = ("light", "dark")
FONT_SIZES = range(7, 33)

T = TypeVar("T")


def default_config_dir() -> Path:
    """Platform-appropriate settings directory (overridable for portable use)."""
    if override := os.environ.get(CONFIG_DIR_ENV):
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / APP_NAME.lower()


def _get(data: dict[str, Any], key: str, default: T) -> T:
    """``data[key]`` if its type is exactly that of *default* (bool is not int), else *default*."""
    value = data.get(key, default)
    return value if type(value) is type(default) else default


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _remember(items: list[str], item: str, limit: int) -> None:
    """Move *item* to the front of an MRU list."""
    if item in items:
        items.remove(item)
    items.insert(0, item)
    del items[limit:]


@dataclass
class Settings:
    theme: str = "light"
    font_size: int = 10
    wrap: bool = False
    hide_empty: bool = False
    side_panel: bool = True
    side_panel_width: int = 300
    geometry: str = ""
    search_regex: bool = False
    search_case_sensitive: bool = False
    search_whole_word: bool = False
    rules: list[Rule] = field(default_factory=default_rules)
    recent_files: list[str] = field(default_factory=list)
    search_history: list[str] = field(default_factory=list)

    def remember_file(self, path: str) -> None:
        _remember(self.recent_files, path, MAX_RECENT_FILES)

    def forget_file(self, path: str) -> None:
        if path in self.recent_files:
            self.recent_files.remove(path)

    def remember_search(self, text: str) -> None:
        if text:
            _remember(self.search_history, text, MAX_SEARCH_HISTORY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "theme": self.theme,
            "font_size": self.font_size,
            "wrap": self.wrap,
            "hide_empty": self.hide_empty,
            "side_panel": self.side_panel,
            "side_panel_width": self.side_panel_width,
            "geometry": self.geometry,
            "search_regex": self.search_regex,
            "search_case_sensitive": self.search_case_sensitive,
            "search_whole_word": self.search_whole_word,
            "rules": [rule.to_dict() for rule in self.rules],
            "recent_files": self.recent_files,
            "search_history": self.search_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        """Build settings from possibly old, partial or hand-edited data."""
        defaults = cls()
        theme = _get(data, "theme", defaults.theme)
        font_size = _get(data, "font_size", defaults.font_size)
        raw_rules = data.get("rules")
        rules = defaults.rules
        if isinstance(raw_rules, list):
            rules = [Rule.from_dict(item) for item in raw_rules if isinstance(item, dict)]
        return cls(
            theme=theme if theme in THEMES else defaults.theme,
            font_size=font_size if font_size in FONT_SIZES else defaults.font_size,
            wrap=_get(data, "wrap", defaults.wrap),
            hide_empty=_get(data, "hide_empty", defaults.hide_empty),
            side_panel=_get(data, "side_panel", defaults.side_panel),
            side_panel_width=max(180, _get(data, "side_panel_width", defaults.side_panel_width)),
            geometry=_get(data, "geometry", defaults.geometry),
            search_regex=_get(data, "search_regex", defaults.search_regex),
            search_case_sensitive=_get(
                data, "search_case_sensitive", defaults.search_case_sensitive
            ),
            search_whole_word=_get(data, "search_whole_word", defaults.search_whole_word),
            rules=rules,
            recent_files=_strings(data.get("recent_files"))[:MAX_RECENT_FILES],
            search_history=_strings(data.get("search_history"))[:MAX_SEARCH_HISTORY],
        )


@dataclass
class FileState:
    """What is remembered about a file between sessions."""

    fingerprint: Fingerprint | None = None
    marks: list[Mark] = field(default_factory=list)
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        fingerprint = self.fingerprint
        return {
            "fingerprint": [fingerprint.length, fingerprint.digest] if fingerprint else None,
            "marks": [mark.to_dict() for mark in self.marks],
            "line": self.line,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileState:
        fingerprint = None
        raw = data.get("fingerprint")
        if isinstance(raw, list) and len(raw) == 2:
            fingerprint = Fingerprint(int(raw[0]), str(raw[1]))
        marks = []
        for item in data.get("marks") or []:
            with contextlib.suppress(KeyError, TypeError, ValueError):
                marks.append(Mark.from_dict(item))
        return cls(fingerprint, marks, max(0, _get(data, "line", 0)))


class SettingsStore:
    """Reads and writes settings (``settings.json``) and per-file state (``files.json``).

    Persistence problems are logged, never raised: losing a preference must not
    stop anybody from reading their logs.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_config_dir()

    @property
    def settings_path(self) -> Path:
        return self.directory / "settings.json"

    @property
    def files_path(self) -> Path:
        return self.directory / "files.json"

    def load(self) -> Settings:
        data = self._read(self.settings_path)
        return Settings.from_dict(data) if data is not None else Settings()

    def save(self, settings: Settings) -> None:
        self._write(self.settings_path, settings.to_dict())

    def load_file_state(self, path: Path) -> FileState | None:
        entry = self._file_states().get(_file_key(path))
        if not isinstance(entry, dict):
            return None
        try:
            return FileState.from_dict(entry)
        except (TypeError, ValueError):
            return None

    def save_file_state(self, path: Path, state: FileState | None) -> None:
        """Store (or with ``None``, forget) the state of *path*."""
        states = self._file_states()
        key = _file_key(path)
        if state is None:
            if states.pop(key, None) is None:
                return
        else:
            states[key] = {**state.to_dict(), "used": time.time()}
            if len(states) > MAX_FILE_STATES:
                by_age = sorted(states, key=lambda k: _get(states[k], "used", 0.0), reverse=True)
                states = {k: states[k] for k in by_age[:MAX_FILE_STATES]}
        self._write(self.files_path, {"version": SCHEMA_VERSION, "files": states})

    def _file_states(self) -> dict[str, Any]:
        data = self._read(self.files_path) or {}
        files = data.get("files")
        return files if isinstance(files, dict) else {}

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable %s: %s", path, exc)
            with contextlib.suppress(OSError):  # keep it for the user, out of the way
                path.replace(path.with_name(path.name + ".corrupt"))
            return None
        return data if isinstance(data, dict) else None

    def _write(self, path: Path, data: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2, ensure_ascii=False)
                    handle.write("\n")
                os.replace(tmp, path)  # atomic: never leaves a half-written file
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        except OSError as exc:
            log.warning("Could not save %s: %s", path, exc)


def _file_key(path: Path) -> str:
    return os.path.normcase(str(path))
