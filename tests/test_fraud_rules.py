"""Rule engine: explainable triggers, lifecycle gating, determinism."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.fraud.models import RiskRule
from apps.fraud.rules import evaluate_rules


def _rule(rule_id, score=30, **kw):
    base = dict(
        rule_id=rule_id, name=rule_id, score=score,
        lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True,
    )
    base.update(kw)
    return RiskRule.objects.create(**base)


@pytest.fixture(autouse=True)
def clean_rules(db):
    yield
    RiskRule.objects.all().delete()


SV = {"NEW_BENEFICIARY": True, "AMOUNT": "9000.00"}


def test_rule_triggers_and_is_explainable(db):
    _rule("NEW_BENEFICIARY_HIGH_VALUE", score=35,
          conditions=[{"signal": "NEW_BENEFICIARY", "op": "is", "value": True}])
    triggered, rsv = evaluate_rules("TRANSFER", SV)
    assert len(triggered) == 1
    t = triggered[0]
    assert t["score"] == 35 and t["rule_id"] == "NEW_BENEFICIARY_HIGH_VALUE"
    assert t["conditions"] == [{"signal": "NEW_BENEFICIARY", "op": "is", "value": True}]
    assert rsv.startswith("rules-")


def test_disabled_rule_never_contributes(db):
    _rule("R1", conditions=[], enabled=False)
    triggered, _ = evaluate_rules("TRANSFER", {})
    assert triggered == []


def test_expired_rule_never_contributes(db):
    _rule("R2", conditions=[],
          effective_until=timezone.now() - timedelta(minutes=1))
    _rule("R3", conditions=[],
          effective_from=timezone.now() + timedelta(minutes=5))
    triggered, _ = evaluate_rules("TRANSFER", {})
    assert [t["rule_id"] for t in triggered] == []


def test_operation_type_filter(db):
    _rule("CARD_ONLY", operation_types=["CARD_PURCHASE"], conditions=[])
    assert evaluate_rules("TRANSFER", {})[0] == []
    assert len(evaluate_rules("CARD_PURCHASE", {})[0]) == 1


def test_all_conditions_must_hold(db):
    _rule("BOTH", conditions=[
        {"signal": "NEW_BENEFICIARY", "op": "is", "value": True},
        {"signal": "AMOUNT", "op": "gt", "value": "5000"},
    ])
    assert evaluate_rules("TRANSFER", {"NEW_BENEFICIARY": True, "AMOUNT": "4000"})[0] == []
    assert len(evaluate_rules("TRANSFER", {"NEW_BENEFICIARY": True, "AMOUNT": "9000"})[0]) == 1


def test_boundary_gt_is_strict(db):
    _rule("GT5000", conditions=[{"signal": "AMOUNT", "op": "gt", "value": "5000"}])
    assert evaluate_rules("TRANSFER", {"AMOUNT": "5000"})[0] == []      # exactly at: no
    assert evaluate_rules("TRANSFER", {"AMOUNT": "5000.01"})[0] != []   # one cent above: yes


def test_failed_signal_value_does_not_trigger(db):
    _rule("ON_AMOUNT", conditions=[{"signal": "AMOUNT", "op": "gt", "value": "100"}])
    sv = {"AMOUNT": {"__error__": "boom"}}
    assert evaluate_rules("TRANSFER", sv)[0] == []


def test_same_inputs_same_result_deterministic_order(db):
    _rule("A", priority=10, conditions=[])
    _rule("B", priority=20, conditions=[])
    r1 = evaluate_rules("TRANSFER", {})
    r2 = evaluate_rules("TRANSFER", {})
    assert r1 == r2
    assert [t["rule_id"] for t in r1[0]] == ["A", "B"]


def test_malformed_condition_or_unknown_operator_never_triggers(db):
    _rule("BAD", conditions={"wrong_key": 1})
    _rule("BADOP", conditions={"signal": "X", "op": "hax", "value": 1})
    assert evaluate_rules("TRANSFER", {"X": 1})[0] == []
