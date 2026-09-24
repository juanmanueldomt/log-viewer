from __future__ import annotations

import codecs
import os
import random

import pytest

from jmlogviewer.core import logfile as logfile_module
from jmlogviewer.core.logfile import FileChange, LogFile, index_lines

from .conftest import MakeFile, index_fully


def all_lines(file: LogFile) -> list[str]:
    return file.read_lines(0, file.line_count)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"", []),
        (b"\n", [""]),
        (b"one", ["one"]),
        (b"one\n", ["one"]),
        (b"one\ntwo", ["one", "two"]),
        (b"one\n\nthree\n", ["one", "", "three"]),
        (b"crlf\r\nline\r\n", ["crlf", "line"]),
        (codecs.BOM_UTF8 + b"bom\nx", ["bom", "x"]),
    ],
)
def test_lines(make_file: MakeFile, content: bytes, expected: list[str]) -> None:
    file = index_fully(LogFile(make_file(content)))
    assert file.line_count == len(expected)
    assert all_lines(file) == expected


def test_random_content_with_tiny_chunks(make_file: MakeFile) -> None:
    rng = random.Random(7)
    lines = ["".join(rng.choice("ab \té") for _ in range(rng.randint(0, 12))) for _ in range(500)]
    file = index_fully(LogFile(make_file("\n".join(lines))), chunk_bytes=5)
    assert file.line_count == len(lines)
    assert all_lines(file) == lines
    assert file.read_lines(100, 105) == lines[100:105]
    assert file.read_line(499) == lines[499]


def test_intermediate_batches_end_on_complete_lines(make_file: MakeFile) -> None:
    path = make_file("aaa\nbbb\nccc")
    batches = [b for b in index_lines(path, 0, 11, chunk_bytes=2, interval=0) if b is not None]
    for batch in batches[:-1]:
        assert batch.end == batch.starts[-1]
    assert batches[-1].end == 11


def test_encoding_fallback(make_file: MakeFile) -> None:
    file = index_fully(LogFile(make_file(b"caf\xe9\n")))
    assert file.encoding == "cp1252"
    assert all_lines(file) == ["café"]


def test_directory_is_rejected(tmp_path: object) -> None:
    with pytest.raises(IsADirectoryError):
        LogFile(str(tmp_path))


def test_long_lines_can_be_capped(make_file: MakeFile, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logfile_module, "BLOCK_READ_LIMIT", 10)
    file = index_fully(LogFile(make_file("x" * 100 + "\nshort\n")))
    assert file.read_lines(0, 2, max_line_bytes=8) == ["x" * 8, "short"]
    assert file.read_lines(0, 2) == ["x" * 100, "short"]


def test_block_end(make_file: MakeFile) -> None:
    file = index_fully(LogFile(make_file("aaaa\n" * 10)))  # 5 bytes per line
    assert file.block_end(0, 10, 12) == 3
    assert file.block_end(0, 10, 1) == 1  # always at least one line
    assert file.block_end(8, 10, 1000) == 10


class TestChanges:
    def test_growth_completes_the_partial_last_line(self, make_file: MakeFile) -> None:
        path = make_file("first\nsec")
        file = index_fully(LogFile(path))
        assert (file.line_count, file.complete_line_count) == (2, 1)
        assert file.check() is FileChange.UNCHANGED

        with path.open("ab") as handle:
            handle.write(b"ond\nthird\n")
        assert file.check() is FileChange.GROWN
        index_fully(file)
        assert all_lines(file) == ["first", "second", "third"]
        assert file.complete_line_count == 3

    def test_truncation(self, make_file: MakeFile) -> None:
        path = make_file("a\nb\nc\n")
        file = index_fully(LogFile(path))
        path.write_bytes(b"a\n")
        assert file.check() is FileChange.REPLACED

    def test_rewritten_head_is_detected_even_if_larger(self, make_file: MakeFile) -> None:
        path = make_file("old content\n")
        file = index_fully(LogFile(path))
        path.write_bytes(b"new content, and much more of it\n")
        assert file.check() is FileChange.REPLACED

    def test_replaced_by_another_file(self, make_file: MakeFile) -> None:
        path = make_file("same\n")
        file = index_fully(LogFile(path))
        other = make_file("same\nplus more\n", name="other.log")
        os.replace(other, path)
        assert file.check() is FileChange.REPLACED

    def test_missing(self, make_file: MakeFile) -> None:
        path = make_file("x\n")
        file = index_fully(LogFile(path))
        path.unlink()
        assert file.check() is FileChange.MISSING

    def test_fingerprint(self, make_file: MakeFile) -> None:
        path = make_file("abc\n")
        file = index_fully(LogFile(path))
        assert file.fingerprint.matches(path)
        with path.open("ab") as handle:
            handle.write(b"appended\n")
        assert file.fingerprint.matches(path)  # appending keeps the head
        path.write_bytes(b"xyz\n")
        assert not file.fingerprint.matches(path)
