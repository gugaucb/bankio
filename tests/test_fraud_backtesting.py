"""Backtesting: replay changes nothing, honest metrics, enforcement gate."""
import pytest

from apps.fraud.backtesting import backtest, enforcement_gate
from apps.fraud.models import RiskEvaluation, RiskRule


@pytest.fixture(autouse=True)
def clean(db):
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield


def _ev(op, signals):
    return RiskEvaluation.objects.create(
        operation_type=op,
        engine_mode=RiskEvaluation.EngineMode.SHADOW,
        decision=RiskEvaluation.Decision.ALLOW,
        risk_score=0, risk_level=RiskEvaluation.RiskLevel.LOW,
        signal_values=signals,
        status=RiskEvaluation.Status.COMPLETED,
        triggered_rules=[],
    )


@pytest.fixture
def history(db):
    for _ in range(9):
        _ev("TRANSFER", {"NEW_BENEFICIARY": False})
    for _ in range(1):
        _ev("TRANSFER", {"NEW_BENEFICIARY": True})


def _candidate(**kw):
    base = dict(rule_id="R-CAND", name="cand", score=65, enabled=True)
    base.update(kw)
    return RiskRule.objects.create(**base)


def test_replay_produces_distribution_without_mutation(history):
    candidate = _candidate(conditions=[{"signal": "NEW_BENEFICIARY", "op": "is", "value": True}])
    result = backtest([candidate])
    assert result["total"] == 10
    # score 65 -> HIGH -> REVIEW under transfer policy
    assert result["decisions"]["REVIEW"] == 1
    assert result["decisions"]["ALLOW"] == 9
    assert result["top_rules"] == {"R-CAND v1": 1}
    # nothing changed
    assert RiskEvaluation.objects.count() == 10
    assert all(e.decision == RiskEvaluation.Decision.ALLOW for e in RiskEvaluation.objects.all())


def test_labels_honestly_reported_unavailable(history):
    result = backtest([])
    assert result["labels_available"] is False
    assert result["precision_recall"] is None
    assert "label" in result["note"].lower()


def test_disabled_candidate_does_not_trigger(history):
    candidate = _candidate(enabled=False, conditions=[{"signal": "NEW_BENEFICIARY", "op": "is", "value": True}])
    result = backtest([candidate])
    assert result["decisions"]["ALLOW"] == 10


def test_enforcement_gate_blocks_aggressive_ruleset(db, history):
    # rule that fires on everything with CRITICAL score -> 100% BLOCK
    hammer = _candidate(rule_id="R-HAMMER", score=100)
    result = backtest([hammer])
    gate = enforcement_gate(result)
    assert gate["pass"] is False and gate["block_rate"] == 1.0

    quiet = _candidate(rule_id="R-QUIET", score=0, conditions=[{"signal": "__never__", "op": "is", "value": 1}])
    gate2 = enforcement_gate(backtest([quiet]))
    assert gate2["pass"] is True


def test_rule_change_gate_requires_backtest_before_enforcement(db, history):
    """§56: a major policy cannot enter enforcement without replay where data permits."""
    candidate = _candidate(conditions=[{"signal": "NEW_BENEFICIARY", "op": "is", "value": True}])
    result = backtest([candidate])
    gate = enforcement_gate(result)
    assert gate["pass"] is True
    # the recorded artifact proves replay ran before cutover
    assert gate["block_rate"] == 0.0
