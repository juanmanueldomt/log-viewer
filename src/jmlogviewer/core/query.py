"""Text/regex queries shared by search, highlight rules and filters."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .scanner import Matcher

EMPTY_LINE_SOURCE = r"^[^\S\n]*$"
"""Regex source matching lines that are empty or contain only whitespace."""

_LEADING_FLAGS = re.compile(r"\(\?([aiLmsux]+)\)")


class QueryError(ValueError):
    """The query is not a valid pattern (e.g. a malformed regular expression)."""


@dataclass(frozen=True, slots=True)
class Query:
    """What to look for in each line of a log."""

    text: str
    regex: bool = False
    case_sensitive: bool = False
    whole_word: bool = False

    def source(self) -> str:
        """Return an equivalent regex source with its flags scoped inline.

        Sources of different queries can be joined with ``|`` into one pattern.
        """
        flags = ""
        if self.regex:
            body = self.text
            if match := _LEADING_FLAGS.match(body):  # "(?i)foo" -> "(?i:foo)"
                flags, body = match.group(1), body[match.end() :]
        else:
            body = re.escape(self.text)
        if self.whole_word:
            body = rf"(?<!\w)(?:{body})(?!\w)"
        if not self.case_sensitive:
            flags += "i"
        flags = "".join(dict.fromkeys(flags))
        return f"(?{flags}:{body})" if flags else f"(?:{body})"

    def compile(self) -> re.Pattern[str]:
        """Compile for matching single lines, e.g. to highlight visible text."""
        return compile_source(self.source())

    def matcher(self) -> Matcher:
        """Compile for scanning many lines as fast as possible."""
        query, fold_case = self, False
        if not self.regex and not self.case_sensitive:
            query = Query(self.text.lower(), case_sensitive=True, whole_word=self.whole_word)
            fold_case = True
        prefilter = None
        if query.whole_word:
            prefilter = Query(query.text, query.regex, query.case_sensitive).compile()
        return Matcher(query.compile(), fold_case=fold_case, prefilter=prefilter)

    def validate(self) -> None:
        """Raise :class:`QueryError` if the query cannot be compiled."""
        self.compile()

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "regex": self.regex,
            "case_sensitive": self.case_sensitive,
            "whole_word": self.whole_word,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Query:
        return cls(
            text=str(data.get("text", "")),
            regex=bool(data.get("regex", False)),
            case_sensitive=bool(data.get("case_sensitive", False)),
            whole_word=bool(data.get("whole_word", False)),
        )


def compile_source(source: str) -> re.Pattern[str]:
    """Compile a regex source with line-oriented flags, as used everywhere."""
    try:
        return re.compile(source, re.MULTILINE)
    except (re.error, OverflowError, RecursionError) as exc:
        message = getattr(exc, "msg", None) or str(exc)
        raise QueryError(f"Invalid pattern: {message}") from exc


def combine(queries: Iterable[Query], *extra_sources: str) -> Matcher | None:
    """Build one matcher that matches a line if any of the queries does."""
    sources = [query.source() for query in queries if query.text]
    sources.extend(extra_sources)
    if not sources:
        return None
    return Matcher(compile_source("|".join(sources)))
