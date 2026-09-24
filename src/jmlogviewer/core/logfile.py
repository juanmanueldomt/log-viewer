"""Line-indexed, read-only access to a (possibly huge and growing) text file."""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
import time
from array import array
from bisect import bisect_left
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import BinaryIO

from .text import decode_block, detect_encoding

INDEX_CHUNK_BYTES = 1 << 20
FINGERPRINT_BYTES = 4096
ENCODING_SAMPLE_BYTES = 1 << 16
BLOCK_READ_LIMIT = 1 << 20
"""Above this many bytes, :meth:`LogFile.read_lines` reads line by line to honour caps."""


class FileChange(Enum):
    """What happened to a file since it was last indexed."""

    UNCHANGED = auto()
    GROWN = auto()  # data was appended
    REPLACED = auto()  # truncated, rotated or rewritten: must be reloaded
    MISSING = auto()  # deleted or renamed (e.g. mid-rotation)


@dataclass(frozen=True, slots=True)
class IndexBatch:
    """Start offsets of the lines found while indexing, up to byte ``end``."""

    starts: array[int]
    end: int


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """Digest of the first bytes of a file, used to recognise it later."""

    length: int
    digest: str

    @classmethod
    def of_bytes(cls, data: bytes) -> Fingerprint:
        return cls(len(data), hashlib.sha1(data, usedforsecurity=False).hexdigest())

    @classmethod
    def of_file(cls, path: Path, length: int = FINGERPRINT_BYTES) -> Fingerprint:
        with path.open("rb") as handle:
            return cls.of_bytes(handle.read(length))

    def matches(self, path: Path) -> bool:
        """Whether the file at *path* still starts with the fingerprinted bytes."""
        try:
            return Fingerprint.of_file(path, self.length) == self
        except OSError:
            return False


def index_lines(
    path: Path,
    start: int,
    end: int,
    *,
    chunk_bytes: int = INDEX_CHUNK_BYTES,
    interval: float = 0.1,
) -> Generator[IndexBatch | None]:
    """Find where lines start in the byte range ``[start, end)`` of a file.

    Yields a batch about every *interval* seconds so a caller can show the
    beginning of a huge file while the rest is still being indexed, and
    ``None`` after the other chunks so a scheduler can interleave other work.
    Intermediate batches end at the last complete line, so a half-indexed line
    is never exposed; the final batch ends at *end* (or earlier if the file shrank).
    """
    starts = array("Q")
    pos = start
    last_emit = time.monotonic()
    with path.open("rb") as handle:
        handle.seek(start)
        while pos < end:
            chunk = handle.read(min(chunk_bytes, end - pos))
            if not chunk:
                break
            append = starts.append
            find = chunk.find
            newline = find(b"\n")
            while newline >= 0:
                append(pos + newline + 1)
                newline = find(b"\n", newline + 1)
            pos += len(chunk)
            now = time.monotonic()
            if starts and now - last_emit >= interval:
                yield IndexBatch(starts, starts[-1])
                starts = array("Q")
                last_emit = now
            else:
                yield None
    yield IndexBatch(starts, pos)


class LogFile:
    """Read-only, line-indexed view of a text file.

    The index (the start offset of every line) is filled through :meth:`apply`
    with batches from :func:`index_lines`, usually produced in a worker thread,
    so opening a huge file is instant and the file may keep growing. Only one
    thread mutates the index; others may read lines that are already indexed.

    Line numbers are 0-based. A final line without a trailing newline counts as
    a line; it may still grow while the file is being written.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        # absolute() rather than resolve(): a symlink such as "current.log" must
        # keep pointing at whatever file it designates after a log rotation.
        self.path = Path(path).expanduser().absolute()
        info = os.stat(self.path)
        if stat.S_ISDIR(info.st_mode):
            raise IsADirectoryError(f"'{self.path}' is a directory")
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"'{self.path}' is not a regular file")
        with self.path.open("rb") as handle:
            sample = handle.read(ENCODING_SAMPLE_BYTES)
        self.encoding, bom_length = detect_encoding(sample)
        self.fingerprint = Fingerprint.of_bytes(sample[:FINGERPRINT_BYTES])
        self._identity = (info.st_dev, info.st_ino)
        self._disk_size = info.st_size
        self._starts = array("Q", [bom_length])
        self._size = bom_length  # bytes indexed so far

    # -- metrics ---------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def indexed_size(self) -> int:
        """Number of bytes covered by the line index."""
        return self._size

    @property
    def disk_size(self) -> int:
        """Size of the file on disk when it was last checked."""
        return self._disk_size

    @property
    def line_count(self) -> int:
        starts = self._starts
        return len(starts) - 1 if starts[-1] == self._size else len(starts)

    @property
    def complete_line_count(self) -> int:
        """Lines terminated by a newline: their content can no longer change."""
        return len(self._starts) - 1

    def line_start(self, line: int) -> int:
        return self._starts[line]

    def line_end(self, line: int) -> int:
        """Offset just past *line*, including its terminator."""
        starts = self._starts
        return starts[line + 1] if line + 1 < len(starts) else self._size

    def block_end(self, first: int, stop: int, max_bytes: int) -> int:
        """End (exclusive) of a run of at least one line from *first*, about *max_bytes* long."""
        starts = self._starts
        return bisect_left(starts, starts[first] + max_bytes, first + 1, stop)

    # -- reading ---------------------------------------------------------------

    @contextmanager
    def reader(self) -> Iterator[BlockReader]:
        """Open the file for a series of reads.

        The file is not kept open between uses: on Windows an open handle would
        prevent the application writing the log from rotating it.
        """
        with self.path.open("rb") as handle:
            yield BlockReader(self, handle)

    def read_lines(self, first: int, stop: int, max_line_bytes: int | None = None) -> list[str]:
        """Display text of lines ``[first, stop)``, see :meth:`BlockReader.read_lines`."""
        if first >= min(stop, self.line_count):
            return []
        with self.reader() as reader:
            return reader.read_lines(first, stop, max_line_bytes)

    def read_line(self, line: int) -> str:
        lines = self.read_lines(line, line + 1)
        return lines[0] if lines else ""

    # -- indexing & change detection ------------------------------------------

    def apply(self, batch: IndexBatch) -> None:
        """Add a batch produced by :func:`index_lines` to the index."""
        if batch.starts:
            self._starts.extend(batch.starts)
        self._size = max(self._size, batch.end)
        self._disk_size = max(self._disk_size, self._size)
        if self.fingerprint.length < min(self._size, FINGERPRINT_BYTES):
            # The file was tiny when opened: fingerprint more of it now.
            with contextlib.suppress(OSError):
                self.fingerprint = Fingerprint.of_file(self.path)

    def check(self) -> FileChange:
        """Compare the file on disk with what has been indexed."""
        try:
            info = os.stat(self.path)
        except OSError:
            return FileChange.MISSING
        self._disk_size = info.st_size
        if (info.st_dev, info.st_ino) != self._identity or info.st_size < self._size:
            return FileChange.REPLACED
        if not self.fingerprint.matches(self.path):
            return FileChange.REPLACED
        if info.st_size > self._size:
            return FileChange.GROWN
        return FileChange.UNCHANGED


class BlockReader:
    """Reads decoded blocks of lines from an open :class:`LogFile`."""

    def __init__(self, file: LogFile, handle: BinaryIO) -> None:
        self._file = file
        self._handle = handle

    def read_block(self, first: int, stop: int) -> str:
        """Lines ``[first, stop)`` as one string, separated by ``\\n``."""
        start = self._file.line_start(first)
        end = self._file.line_end(stop - 1)
        self._handle.seek(start)
        return decode_block(self._handle.read(end - start), self._file.encoding)

    def read_lines(self, first: int, stop: int, max_line_bytes: int | None = None) -> list[str]:
        """Return the display text of lines ``[first, stop)``.

        With *max_line_bytes*, only that many bytes of each line are read, which
        keeps pathological lines (megabytes of JSON) cheap to display.
        """
        file = self._file
        stop = min(stop, file.line_count)
        if first >= stop:
            return []
        span = file.line_end(stop - 1) - file.line_start(first)
        if max_line_bytes is None or span <= BLOCK_READ_LIMIT:
            lines = self.read_block(first, stop).split("\n")
        else:
            lines = [self.read_prefix(line, max_line_bytes) for line in range(first, stop)]
        missing = (stop - first) - len(lines)
        if missing > 0:  # the file shrank under our feet; a reload is coming
            lines.extend([""] * missing)
        return lines

    def read_prefix(self, line: int, max_bytes: int) -> str:
        """At most the first *max_bytes* of a line."""
        start = self._file.line_start(line)
        end = min(self._file.line_end(line), start + max_bytes)
        self._handle.seek(start)
        return decode_block(self._handle.read(end - start), self._file.encoding)
