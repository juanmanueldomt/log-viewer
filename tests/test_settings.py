from __future__ import annotations

import json
from pathlib import Path

import pytest

from jmlogviewer.core import settings as settings_module
from jmlogviewer.core.logfile import Fingerprint
from jmlogviewer.core.marks import Mark
from jmlogviewer.core.query import Query
from jmlogviewer.core.rules import Rule
from jmlogviewer.core.settings import (
    CONFIG_DIR_ENV,
    FileState,
    Settings,
    SettingsStore,
    default_config_dir,
)


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "config")


def test_defaults_when_nothing_saved(store: SettingsStore) -> None:
    settings = store.load()
    assert settings == Settings()
    assert settings.rules  # starts with useful rules


def test_round_trip(store: SettingsStore) -> None:
    settings = Settings(theme="dark", font_size=14, wrap=True, rules=[Rule(Query("x"))])
    settings.remember_file("/var/log/a.log")
    settings.remember_search("timeout")
    store.save(settings)
    assert store.load() == settings


def test_empty_rule_list_is_kept(store: SettingsStore) -> None:
    store.save(Settings(rules=[]))
    assert store.load().rules == []


def test_invalid_values_fall_back_to_defaults(store: SettingsStore) -> None:
    store.directory.mkdir(parents=True)
    store.settings_path.write_text(
        json.dumps({"theme": "neon", "font_size": True, "wrap": "yes", "recent_files": [1, "a"]})
    )
    settings = store.load()
    assert settings.theme == "light"
    assert settings.font_size == 10
    assert settings.wrap is False
    assert settings.recent_files == ["a"]


def test_corrupt_file_is_set_aside(store: SettingsStore) -> None:
    store.directory.mkdir(parents=True)
    store.settings_path.write_text("{not json")
    assert store.load() == Settings()
    assert not store.settings_path.exists()
    assert store.settings_path.with_name("settings.json.corrupt").exists()


def test_recent_files_are_most_recent_first_and_capped() -> None:
    settings = Settings()
    for i in range(15):
        settings.remember_file(f"/f{i}")
    settings.remember_file("/f3")
    assert settings.recent_files[0] == "/f3"
    assert len(settings.recent_files) == settings_module.MAX_RECENT_FILES
    settings.forget_file("/f3")
    assert "/f3" not in settings.recent_files


class TestFileState:
    def test_round_trip(self, store: SettingsStore, tmp_path: Path) -> None:
        path = tmp_path / "a.log"
        state = FileState(Fingerprint(4, "abc"), [Mark(1, 3, label="x")], line=42)
        store.save_file_state(path, state)
        loaded = store.load_file_state(path)
        assert loaded is not None
        assert loaded.fingerprint == state.fingerprint
        assert loaded.line == 42
        assert [(m.first, m.last, m.label) for m in loaded.marks] == [(1, 3, "x")]

    def test_forget(self, store: SettingsStore, tmp_path: Path) -> None:
        path = tmp_path / "a.log"
        store.save_file_state(path, FileState())
        store.save_file_state(path, None)
        assert store.load_file_state(path) is None

    def test_oldest_states_are_pruned(
        self, store: SettingsStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings_module, "MAX_FILE_STATES", 3)
        for i in range(5):
            store.save_file_state(tmp_path / f"{i}.log", FileState(line=i))
        assert store.load_file_state(tmp_path / "0.log") is None
        assert store.load_file_state(tmp_path / "4.log") is not None

    def test_bad_marks_are_skipped(self) -> None:
        state = FileState.from_dict({"marks": [{"first": 1}, {"nope": 2}, "junk"]})
        assert [m.first for m in state.marks] == [1]


def test_config_dir_can_be_overridden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    assert default_config_dir() == tmp_path


def test_unwritable_directory_does_not_raise(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    SettingsStore(blocker / "sub").save(Settings())  # logged, not raised
