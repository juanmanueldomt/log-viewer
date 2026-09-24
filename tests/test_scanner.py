from __future__ import annotations

import random
import re

import pytest

from jmlogviewer.core.logfile import LogFile
from jmlogviewer.core.query import Query
from jmlogviewer.core.scanner import (
    Matcher,
    MatchScanner,
    ScanUpdate,
    VisibleScanner,
    scan_lines,
)

from .conftest import MakeFile, index_fully

WORDS = ["INFO", "info", "ERROR", "error", "WARN", "a", "b", "", " ", "\t", "x-1", "été"]


def naive(pattern: str, text: str, flags: int = 0) -> list[int]:
    compiled = re.compile(pattern, re.MULTILINE | flags)
    return [i for i, line in enumerate(text.split("\n")) if compiled.search(line)]


def random_text(seed: int, lines: int = 300) -> str:
    rng = random.Random(seed)
    return "\n".join(
        " ".join(rng.choice(WORDS) for _ in range(rng.randint(0, 6))) for _ in range(lines)
    )


PATTERNS = [
    "ERROR",
    r"\berror\b",
    "^$",
    r"^\s*$",
    r"\s+",
    "a b",
    r"a\s+b",  # could match across a newline: must not
    r"(?s)WARN.*INFO",  # DOTALL: a match may run into the next line
    "$",
    "x-1$",
    "(?i)warn",
    "été",
]


class TestMatchingLines:
    @pytest.mark.parametrize("pattern", PATTERNS)
    @pytest.mark.parametrize("dense", [False, True])
    @pytest.mark.parametrize("seed", range(5))
    def test_same_as_line_by_line(self, pattern: str, dense: bool, seed: int) -> None:
        text = random_text(seed)
        matcher = Matcher(re.compile(pattern, re.MULTILINE))
        assert matcher.matching_lines(text, 0, dense=dense).tolist() == naive(pattern, text)

    def test_line_numbers_are_offset_by_first(self) -> None:
        matcher = Matcher(re.compile("x", re.MULTILINE))
        assert matcher.matching_lines("a\nx\nb\nx", 10).tolist() == [11, 13]

    def test_a_line_is_reported_once(self) -> None:
        matcher = Matcher(re.compile("a", re.MULTILINE))
        assert matcher.matching_lines("aaa\nbab\nccc", 0).tolist() == [0, 1]

    def test_no_match_across_lines(self) -> None:
        matcher = Matcher(re.compile(r"foo\sbar", re.MULTILINE))
        assert matcher.matching_lines("foo\nbar\nfoo bar", 0).tolist() == [2]

    def test_empty_text_is_one_empty_line(self) -> None:
        matcher = Matcher(re.compile("^$", re.MULTILINE))
        assert matcher.matching_lines("", 5).tolist() == [5]

    def test_folded_case(self) -> None:
        matcher = Query("error").matcher()
        assert matcher.fold_case
        assert matcher.matching_lines("ERROR\nok\nError", 0).tolist() == [0, 2]


class TestScanners:
    def test_match_scanner_switches_strategy_on_dense_hits(self) -> None:
        scanner = MatchScanner(Matcher(re.compile("x", re.MULTILINE)))
        dense_text = "\n".join(["x"] * 10)
        assert scanner(dense_text, 0, 10).tolist() == list(range(10))
        assert scanner._dense
        sparse_text = "\n".join(["a"] * 9 + ["x"])
        assert scanner(sparse_text, 10, 20).tolist() == [19]
        assert not scanner._dense

    def test_visible_with_exclude_only(self) -> None:
        exclude = Matcher(re.compile("noise", re.MULTILINE))
        scanner = VisibleScanner(None, exclude)
        assert scanner("a\nnoise\nb\nnoise", 3, 7).tolist() == [3, 5]

    def test_visible_with_include_and_exclude(self) -> None:
        include = Matcher(re.compile("ERROR", re.MULTILINE))
        exclude = Matcher(re.compile("ignored", re.MULTILINE))
        scanner = VisibleScanner(include, exclude)
        text = "ERROR 1\nINFO\nERROR ignored\nERROR 2"
        assert scanner(text, 0, 4).tolist() == [0, 3]


def updates_of(steps: object) -> list[ScanUpdate]:
    return [step for step in steps if step is not None]  # type: ignore[attr-defined]


class TestScanLines:
    def test_matches_naive_across_chunk_boundaries(self, make_file: MakeFile) -> None:
        text = random_text(42, lines=2000)
        file = index_fully(LogFile(make_file(text + "\n")))
        matcher = Matcher(re.compile("ERROR", re.MULTILINE))
        steps = scan_lines(
            file, 0, file.line_count, MatchScanner(matcher), chunk_bytes=64, interval=0
        )
        updates = updates_of(steps)
        found = [line for update in updates for line in update.lines]
        assert found == naive("ERROR", text)
        assert len(updates) > 10
        assert updates[-1].upto == file.line_count

    def test_yields_heartbeats_between_reports(self, make_file: MakeFile) -> None:
        file = index_fully(LogFile(make_file("x\n" * 1000)))
        scanner = MatchScanner(Matcher(re.compile("x", re.MULTILINE)))
        steps = list(scan_lines(file, 0, 1000, scanner, chunk_bytes=64, interval=3600))
        assert len(steps) > 1
        assert all(step is None for step in steps[:-1])
        assert isinstance(steps[-1], ScanUpdate)
        assert len(steps[-1].lines) == 1000

    def test_partial_range(self, make_file: MakeFile) -> None:
        file = index_fully(LogFile(make_file("x\ny\nx\ny\nx\n")))
        scanner = MatchScanner(Matcher(re.compile("x", re.MULTILINE)))
        updates = updates_of(scan_lines(file, 1, 4, scanner))
        assert [line for u in updates for line in u.lines] == [2]

    def test_empty_range_reports_once(self, make_file: MakeFile) -> None:
        file = index_fully(LogFile(make_file("x\n")))
        scanner = MatchScanner(Matcher(re.compile("x", re.MULTILINE)))
        updates = updates_of(scan_lines(file, 1, 1, scanner))
        assert len(updates) == 1
        assert updates[0].upto == 1
        assert not updates[0].lines


class TestPrefilter:
    @pytest.mark.parametrize(
        "query",
        [
            Query("error", whole_word=True),
            Query("ERROR", case_sensitive=True, whole_word=True),
            Query("WARN|a", regex=True, whole_word=True),
            Query("x-1", whole_word=True),
            Query("(?i)info", regex=True, case_sensitive=True, whole_word=True),
        ],
    )
    @pytest.mark.parametrize("dense", [False, True])
    @pytest.mark.parametrize("seed", range(4))
    def test_whole_word_scans_match_line_by_line(
        self, query: Query, dense: bool, seed: int
    ) -> None:
        text = random_text(seed)
        matcher = query.matcher()
        assert matcher.prefilter is not None
        expected = naive(query.source(), text)
        assert matcher.matching_lines(text, 0, dense=dense).tolist() == expected

    def test_candidates_are_confirmed(self) -> None:
        matcher = Query("err", whole_word=True).matcher()
        assert matcher.matching_lines("error\nerr!\nterrible err", 0).tolist() == [1, 2]
        assert not matcher.matches("errors")
        assert matcher.matches("an err")
