from __future__ import annotations

from jmlogviewer.core.query import Query
from jmlogviewer.core.rules import RULE_COLORS, Rule, RuleAction, default_rules, next_color


def test_default_rules_are_valid() -> None:
    for rule in default_rules():
        rule.query.validate()
        assert rule.highlights


def test_default_error_rule_matches_levels_only() -> None:
    pattern = default_rules()[0].query.compile()
    assert pattern.search("2024-01-01 ERROR boom")
    assert not pattern.search("no errors here")
    assert not pattern.search("ERRORS")


def test_round_trip() -> None:
    rule = Rule(Query("x", regex=True), color="#00FF00", action=RuleAction.HIDE, enabled=False)
    assert Rule.from_dict(rule.to_dict()) == rule


def test_from_dict_is_tolerant() -> None:
    rule = Rule.from_dict({"text": "x", "action": "explode", "color": "red"})
    assert rule.action is RuleAction.LINE
    assert rule.color == RULE_COLORS[0]


def test_hides_and_highlights() -> None:
    hide = Rule(Query("x"), action=RuleAction.HIDE)
    assert hide.hides
    assert not hide.highlights
    assert not hide.with_changes(enabled=False).hides


def test_next_color_skips_used_colors() -> None:
    rules = [Rule(Query("a"), color=RULE_COLORS[0]), Rule(Query("b"), color=RULE_COLORS[1])]
    assert next_color(rules) == RULE_COLORS[2]
    everything = [Rule(Query(str(i)), color=c) for i, c in enumerate(RULE_COLORS)]
    assert next_color(everything) in RULE_COLORS
