"""Engine modes: shadow never interferes; mode changes are controlled + audited."""
import pytest

from apps.audit.models import AuditLog
from apps.fraud import modes
from apps.fraud.engine import evaluate_operation
from apps.fraud.models import FraudEngineSetting, RiskEvaluation
from django.utils import timezone
from decimal import Decimal


@pytest.fixture(autouse=True)
def clean(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    FraudEngineSetting.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield
    FraudEngineSetting.objects.all().delete()


def _ctx():
    from apps.fraud.context import RiskContext

    return RiskContext(operation_type="TRANSFER", amount=Decimal("100"), timestamp=timezone.now())


def _evaluation(decision, mode):
    return RiskEvaluation.objects.create(
        operation_type="TRANSFER", engine_mode=mode, decision=decision,
        risk_level=RiskEvaluation.RiskLevel.CRITICAL,
    )


def test_shadow_mode_records_but_never_interferes(db):
    ev = evaluate_operation(_ctx())  # even a CRITICAL-risk op
    assert modes.effective_decision(ev) == RiskEvaluation.Decision.ALLOW


def test_challenge_mode_downgrades_review_and_block_to_challenge(db):
    for decision in (RiskEvaluation.Decision.REVIEW, RiskEvaluation.Decision.BLOCK):
        ev = _evaluation(decision, RiskEvaluation.EngineMode.CHALLENGE_ONLY)
        assert modes.effective_decision(ev) == RiskEvaluation.Decision.CHALLENGE


def test_enforcement_mode_keeps_decisions(db):
    ev = _evaluation(RiskEvaluation.Decision.BLOCK, RiskEvaluation.EngineMode.ENFORCEMENT)
    assert modes.effective_decision(ev) == RiskEvaluation.Decision.BLOCK


def test_disabled_mode_does_not_interfere(db):
    ev = _evaluation(RiskEvaluation.Decision.BLOCK, "DISABLED")
    assert modes.effective_decision(ev) == RiskEvaluation.Decision.ALLOW


def test_mode_change_is_audited_and_invalid_rejected(db, django_user_model):
    admin = django_user_model.objects.create_user(
        "fraud-admin", email="fa@t.io", password="x", role="FRAUD_MANAGER")
    modes.set_mode(RiskEvaluation.EngineMode.ENFORCEMENT, actor=admin)
    assert modes.get_mode() == "ENFORCEMENT"
    event = AuditLog.objects.get(action="FRAUD_MODE_CHANGED")
    assert event.metadata["to"] == "ENFORCEMENT" and event.actor_id == admin.pk

    with pytest.raises(modes.FraudModeError):
        modes.set_mode("CHAOS")


def test_evaluation_uses_configured_mode(db, settings):
    settings.FRAUD_MODE = None
    modes.set_mode(RiskEvaluation.EngineMode.ENFORCEMENT)
    from apps.fraud.context import RiskContext

    ctx = RiskContext(operation_type="TRANSFER", timestamp=timezone.now())
    ev = evaluate_operation(ctx)
    ev.refresh_from_db()
    assert ev.engine_mode == RiskEvaluation.EngineMode.ENFORCEMENT
