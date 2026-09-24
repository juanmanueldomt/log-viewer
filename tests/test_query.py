from __future__ import annotations

import pytest

from jmlogviewer.core.query import EMPTY_LINE_SOURCE, Query, QueryError, combine


def matches(query: Query, line: str) -> bool:
    return query.compile().search(line) is not None and query.matcher().matches(line)


class TestQuery:
    def test_plain_text_is_literal(self) -> None:
        query = Query("a.b")
        assert matches(query, "xa.by")
        assert not matches(query, "axb")

    def test_case_insensitive_by_default(self) -> None:
        assert matches(Query("error"), "An ERROR occurred")
        assert not matches(Query("error", case_sensitive=True), "An ERROR occurred")

    def test_plain_case_insensitive_scans_with_folded_case(self) -> None:
        assert Query("Error").matcher().fold_case
        assert not Query("Error", regex=True).matcher().fold_case
        assert not Query("Error", case_sensitive=True).matcher().fold_case

    def test_whole_word(self) -> None:
        query = Query("err", whole_word=True)
        assert matches(query, "an err here")
        assert not matches(query, "an error here")

    def test_whole_word_with_symbols_at_the_edges(self) -> None:
        query = Query("[x]", whole_word=True)
        assert matches(query, "flag [x] set")
        assert not matches(query, "flag a[x] set")

    def test_regex(self) -> None:
        query = Query(r"took \d{3,}ms", regex=True)
        assert matches(query, "request took 1500ms")
        assert not matches(query, "request took 15ms")

    def test_leading_inline_flags_are_scoped(self) -> None:
        query = Query("(?i)warn", regex=True, case_sensitive=True)
        assert query.source().startswith("(?i:")
        assert matches(query, "WARN")

    @pytest.mark.parametrize("text", ["(unclosed", "a(?i)b", "*"])
    def test_invalid_regex_raises_query_error(self, text: str) -> None:
        with pytest.raises(QueryError, match="Invalid pattern"):
            Query(text, regex=True).compile()

    def test_dict_round_trip(self) -> None:
        query = Query("x+", regex=True, case_sensitive=True, whole_word=True)
        assert Query.from_dict(query.to_dict()) == query


class TestCombine:
    def test_any_query_matches(self) -> None:
        matcher = combine([Query("alpha"), Query("BETA", case_sensitive=True)])
        assert matcher is not None
        assert matcher.matches("x ALPHA y")
        assert matcher.matches("BETA")
        assert not matcher.matches("beta")

    def test_nothing_to_combine(self) -> None:
        assert combine([]) is None
        assert combine([Query("")]) is None

    def test_extra_sources(self) -> None:
        matcher = combine([], EMPTY_LINE_SOURCE)
        assert matcher is not None
        assert matcher.matches("")
        assert matcher.matches(" \t ")
        assert not matcher.matches(" x ")
