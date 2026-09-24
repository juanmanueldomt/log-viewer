from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from jmlogviewer.core.logfile import LogFile, index_lines
from jmlogviewer.core.session import LogSession
from jmlogviewer.core.tasks import ManualExecutor

MakeFile = Callable[..., Path]


def index_fully(file: LogFile, **kwargs: object) -> LogFile:
    """Index everything on disk synchronously (what the session does in the background)."""
    for batch in index_lines(file.path, file.indexed_size, file.disk_size, **kwargs):  # type: ignore[arg-type]
        if batch is not None:
            file.apply(batch)
    return file


@pytest.fixture
def make_file(tmp_path: Path) -> MakeFile:
    def make(content: bytes | str, name: str = "test.log") -> Path:
        path = tmp_path / name
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return path

    return make


@pytest.fixture
def executor() -> ManualExecutor:
    return ManualExecutor()


@pytest.fixture
def session(executor: ManualExecutor) -> LogSession:
    return LogSession(executor)
