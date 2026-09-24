"""Decoding raw file bytes into the text shown (and searched) by the viewer."""

from __future__ import annotations

import codecs
import re

FALLBACK_ENCODING = "cp1252"

_UTF16_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)

# CSI sequences (colours, cursor movement), OSC sequences (titles, hyperlinks)
# and the remaining two-character escapes.
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])")
_CONTROL_CHAR = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_CONTROL_PICTURES = {c: 0x2400 + c for c in range(0x20) if c not in (0x09, 0x0A)} | {0x7F: 0x2421}


class UnsupportedEncodingError(ValueError):
    """The file uses an encoding the line index cannot handle (e.g. UTF-16)."""


def detect_encoding(sample: bytes) -> tuple[str, int]:
    """Guess the encoding of a file from its first bytes.

    Returns ``(encoding, bom_length)``. UTF-8 (with or without BOM) is preferred;
    anything that is not valid UTF-8 falls back to Windows-1252, which can decode
    any byte. UTF-16/32 files are rejected because their newlines are not single
    ``\\n`` bytes.
    """
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8", len(codecs.BOM_UTF8)
    if sample.startswith(_UTF16_BOMS):
        raise UnsupportedEncodingError("UTF-16 and UTF-32 encoded files are not supported.")
    try:
        # An incremental decoder tolerates a multi-byte character cut by the sample end.
        codecs.getincrementaldecoder("utf-8")().decode(sample, final=False)
    except UnicodeDecodeError:
        return FALLBACK_ENCODING, 0
    return "utf-8", 0


def to_display(text: str) -> str:
    """Make decoded file text safe and readable on screen.

    ANSI escape sequences (terminal colours) are dropped and other control
    characters, except tab and newline, become visible "control pictures"
    (NUL is shown as ``␀``, a stray carriage return as ``␍``).
    """
    if "\x1b" in text:
        text = _ANSI_ESCAPE.sub("", text)
    if _CONTROL_CHAR.search(text):
        text = text.translate(_CONTROL_PICTURES)
    return text


def decode_block(raw: bytes, encoding: str) -> str:
    """Decode a run of whole lines into display text.

    The result has the lines separated by ``\\n``: line terminators (``\\n`` or
    ``\\r\\n``) are normalised and the terminator of the last line is removed, so
    a block of *n* lines always contains exactly ``n - 1`` newlines.
    """
    text = raw.decode(encoding, "replace")
    if text.endswith("\n"):
        text = text[:-1]
    if "\r" in text:
        text = text.replace("\r\n", "\n")
        if text.endswith("\r"):
            text = text[:-1]
    return to_display(text)
