from __future__ import annotations

import codecs

import pytest

from jmlogviewer.core.text import (
    FALLBACK_ENCODING,
    UnsupportedEncodingError,
    decode_block,
    detect_encoding,
    to_display,
)


class TestDetectEncoding:
    def test_ascii_is_utf8(self) -> None:
        assert detect_encoding(b"plain text\n") == ("utf-8", 0)

    def test_utf8_bom_is_skipped(self) -> None:
        assert detect_encoding(codecs.BOM_UTF8 + b"x") == ("utf-8", 3)

    def test_multibyte_char_cut_by_sample_end_is_still_utf8(self) -> None:
        assert detect_encoding("café".encode()[:-1]) == ("utf-8", 0)

    def test_invalid_utf8_falls_back(self) -> None:
        assert detect_encoding(b"caf\xe9 au lait\n") == (FALLBACK_ENCODING, 0)

    @pytest.mark.parametrize("bom", [codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE])
    def test_utf16_is_rejected(self, bom: bytes) -> None:
        with pytest.raises(UnsupportedEncodingError):
            detect_encoding(bom + "x".encode("utf-16-le"))


class TestToDisplay:
    def test_plain_text_is_unchanged(self) -> None:
        assert to_display("a\tb\nc") == "a\tb\nc"

    def test_ansi_colors_are_removed(self) -> None:
        assert to_display("\x1b[31;1mERROR\x1b[0m done") == "ERROR done"

    def test_osc_hyperlinks_are_removed(self) -> None:
        text = "\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\"
        assert to_display(text) == "link"

    def test_control_characters_become_visible(self) -> None:
        assert to_display("a\x00b\rc\x7f") == "a␀b␍c␡"


class TestDecodeBlock:
    def test_last_terminator_is_removed(self) -> None:
        assert decode_block(b"a\nb\n", "utf-8") == "a\nb"

    def test_crlf_is_normalized(self) -> None:
        assert decode_block(b"a\r\nb\r\n", "utf-8") == "a\nb"

    def test_empty_lines_are_kept(self) -> None:
        assert decode_block(b"a\r\n\r\n", "utf-8").split("\n") == ["a", ""]

    def test_invalid_bytes_are_replaced(self) -> None:
        assert decode_block(b"a\xffb", "utf-8") == "a�b"
