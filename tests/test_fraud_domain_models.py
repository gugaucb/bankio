"""Fraud domain model invariants: separation of concepts, DB-level bounds."""
from django.db import IntegrityError
from django.utils import timezone
import pytest

from apps.fraud.models import FraudAlert, FraudCase, RiskEvaluation, RiskRule, RiskSignal


def _eval(**kw):
    base = dict(operation_type="TRANSFER", engine_mode=RiskEvaluation.EngineMode.SHADOW)
    base.update(kw)
    return RiskEvaluation.objects.create(**base)


def test_evaluation_defaults_pending_and_records_versions():
    e = _eval(policy_version="policy-v1", ruleset_version="rules-v1")
    assert e.decision == RiskEvaluation.Decision.PENDING
    assert e.status == RiskEvaluation.Status.EVALUATING


@pytest.mark.django_db
def test_score_above_100_rejected_at_db():
    with pytest.raises(IntegrityError):
        _eval(risk_score=101, decision="ALLOW")


@pytest.mark.django_db
def test_signals_are_facts_unique_per_evaluation():
    from django.db import transaction

    e = _eval()
    RiskSignal.objects.create(evaluation=e, signal_id="NEW_BENEFICIARY", value=True)
    RiskSignal.objects.create(evaluation=e, signal_id="AMOUNT", value="9000.00")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            RiskSignal.objects.create(evaluation=e, signal_id="NEW_BENEFICIARY", value=False)
    assert e.signals.count() == 2


def test_rule_versions_unique():
    RiskRule.objects.create(rule_id="NEW_BENEFICIARY_HIGH_VALUE", version=1, score=35)
    RiskRule.objects.create(rule_id="NEW_BENEFICIARY_HIGH_VALUE", version=2, score=40)
    with pytest.raises(IntegrityError):
        RiskRule.objects.create(rule_id="NEW_BENEFICIARY_HIGH_VALUE", version=2, score=50)


def test_alert_is_not_case_and_not_fraud():
    """INVARIANT 6: alerts/cases exist independently; nothing here confirms fraud."""
    alert = FraudAlert.objects.create(alert_type="RULE_TRIGGERED", severity="HIGH")
    assert alert.status == FraudAlert.Status.OPEN
    case = FraudCase.objects.create(severity="HIGH")
    case.alerts.add(alert)
    assert case.status == FraudCase.Status.OPEN
    assert not hasattr(case, "confirmed_fraud")  # confirmation is a state transition, task 15


def test_case_reference_generated_and_close_requires_reason():
    case = FraudCase.objects.create()
    assert case.case_reference
    case.status = FraudCase.Status.CLOSED
    case.closed_at = timezone.now()
    with pytest.raises(IntegrityError):
        case.save()
