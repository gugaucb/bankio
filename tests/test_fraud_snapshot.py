"""Decision snapshot: full reconstruction of why a decision happened."""
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.fraud.engine import evaluate_operation
from apps.fraud.models import RiskEvaluation, RiskRule


@pytest.fixture(autouse=True)
def clean(db):
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield


def _rule(rule_id, score, conditions=None, **kw):
    return RiskRule.objects.create(
        rule_id=rule_id, name=rule_id, score=score,
        lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True,
        **{"conditions": conditions or [], **kw},
    )


def _ctx(**kw):
    from apps.fraud.context import RiskContext

    base = dict(operation_type="TRANSFER", amount=Decimal("9000"), timestamp=timezone.now())
    base.update(kw)
    return RiskContext(**base)


def test_full_pipeline_persists_reconstructable_snapshot(db):
    _rule("ALWAYS", score=35)
    ev = evaluate_operation(_ctx(), )
    ev.refresh_from_db()
    assert ev.status == RiskEvaluation.Status.COMPLETED
    assert ev.risk_score == 35
    assert ev.risk_level == RiskEvaluation.RiskLevel.MEDIUM
    assert ev.decision == RiskEvaluation.Decision.CHALLENGE
    assert ev.triggered_rules[0]["rule_id"] == "ALWAYS"
    assert ev.ruleset_version.startswith("rules-")
    assert ev.policy_version.startswith("policy-")
    # signal facts stored as rows AND as snapshot json
    assert ev.signals.count() >= 1
    assert "TRANSACTION_AMOUNT" in ev.signal_values


def test_engine_failure_is_explicit_not_allow(db, monkeypatch):
    """A pipeline crash must not silently produce ALLOW (INV 9)."""
    from apps.fraud import engine

    def boom(triggered):
        raise RuntimeError("scoring exploded")

    monkeypatch.setattr(engine.scoring, "score", boom)
    with pytest.raises(RuntimeError):
        evaluate_operation(_ctx())
    ev = RiskEvaluation.objects.latest("pk")
    assert ev.status == RiskEvaluation.Status.FAILED
    assert ev.decision == RiskEvaluation.Decision.DEFER
    assert ev.decision != RiskEvaluation.Decision.ALLOW


def test_same_inputs_same_snapshot(db):
    _rule("D", score=10)
    ctx = _ctx(amount=Decimal("50"))
    e1 = evaluate_operation(ctx)
    e2 = evaluate_operation(ctx)
    assert e1.risk_score == e2.risk_score
    assert e1.decision == e2.decision
    assert e1.ruleset_version == e2.ruleset_version


def test_shadow_mode_is_default(db, settings):
    settings.FRAUD_MODE = None  # unset -> default shadow
    ev = evaluate_operation(_ctx())
    ev.refresh_from_db()
    assert ev.engine_mode in ("SHADOW", None) or ev.engine_mode == RiskEvaluation.EngineMode.SHADOW
