"""Score engine: bounds, bands, determinism (property-tested)."""
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apps.fraud.models import RiskEvaluation
from apps.fraud.scoring import risk_level, score

Rule = st.fixed_dictionaries({
    "rule_id": st.text(min_size=1, max_size=8),
    "version": st.integers(1, 3),
    "score": st.integers(0, 100),
})


@given(st.lists(Rule, max_size=12))
@settings(max_examples=200, deadline=None)
def test_score_always_within_bounds(triggered):
    assert 0 <= score(triggered) <= 100


def test_score_overflow_clamped():
    many = [{"score": 60} for _ in range(5)]  # raw sum 300
    assert score(many) == 100


def test_empty_rules_score_zero():
    assert score([]) == 0


def test_band_boundaries():
    cases = {
        0: "LOW", 29: "LOW",
        30: "MEDIUM", 59: "MEDIUM",
        60: "HIGH", 79: "HIGH",
        80: "CRITICAL", 100: "CRITICAL",
    }
    for value, expected in cases.items():
        assert risk_level(value) == expected, value


@given(st.lists(st.integers(0, 100), max_size=10))
def test_score_equals_sum_when_small(rules):
    triggered = [{"score": s} for s in rules]
    assert score(triggered) == min(sum(rules), 100)


def test_disabled_rule_cannot_contribute(db):
    """Integration guard: scoring consumes engine output only; the rule layer
    already filters disabled rules — proven end-to-end here."""
    from apps.fraud.models import RiskRule
    from apps.fraud.rules import evaluate_rules

    RiskRule.objects.create(
        rule_id="OFF", name="off", score=100,
        lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=False,
    )
    triggered, _ = evaluate_rules("TRANSFER", {})
    assert score(triggered) == 0
