"""Highlight rules: color, or hide, the lines matching an expression."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .query import Query

RULE_COLORS = (
    "#E5484D",  # red
    "#F76B15",  # orange
    "#FFC53D",  # amber
    "#46A758",  # green
    "#12A594",  # teal
    "#0090FF",  # blue
    "#8E4EC6",  # purple
    "#D6409F",  # pink
    "#8B8D98",  # gray
)


class RuleAction(StrEnum):
    LINE = "line"  # tint the whole line
    MATCH = "match"  # tint only the matching text
    HIDE = "hide"  # remove matching lines from the view

    @property
    def label(self) -> str:
        return _ACTION_LABELS[self]


_ACTION_LABELS = {
    RuleAction.LINE: "Highlight line",
    RuleAction.MATCH: "Highlight match",
    RuleAction.HIDE: "Hide line",
}


@dataclass(frozen=True, slots=True)
class Rule:
    """An expression and what to do with the lines that match it."""

    query: Query
    color: str = RULE_COLORS[0]
    action: RuleAction = RuleAction.LINE
    enabled: bool = True

    @property
    def highlights(self) -> bool:
        return self.enabled and self.action is not RuleAction.HIDE

    @property
    def hides(self) -> bool:
        return self.enabled and self.action is RuleAction.HIDE

    def with_changes(self, **changes: Any) -> Rule:
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.query.to_dict(),
            "color": self.color,
            "action": self.action.value,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        try:
            action = RuleAction(data.get("action", RuleAction.LINE))
        except ValueError:
            action = RuleAction.LINE
        color = data.get("color")
        return cls(
            query=Query.from_dict(data),
            color=color if isinstance(color, str) and _is_hex_color(color) else RULE_COLORS[0],
            action=action,
            enabled=bool(data.get("enabled", True)),
        )


def default_rules() -> list[Rule]:
    """Rules a new user starts with: log levels that usually matter."""
    return [
        Rule(
            Query("ERROR|FATAL|CRITICAL|SEVERE", regex=True, case_sensitive=True, whole_word=True),
            color=RULE_COLORS[0],
        ),
        Rule(
            Query("WARN|WARNING", regex=True, case_sensitive=True, whole_word=True),
            color=RULE_COLORS[2],
        ),
    ]


def next_color(rules: list[Rule]) -> str:
    """The first palette color not used yet (cycling when all are taken)."""
    used = {rule.color.upper() for rule in rules}
    for color in RULE_COLORS:
        if color.upper() not in used:
            return color
    return RULE_COLORS[len(rules) % len(RULE_COLORS)]


def _is_hex_color(value: str) -> bool:
    if len(value) != 7 or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True
