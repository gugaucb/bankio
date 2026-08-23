"""Alerts: visibility-only, deduplicated, lifecycle-managed."""
import pytest
from django.utils import timezone

from apps.fraud.alerts import raise_alert
from apps.fraud.models import FraudAlert, RiskEvaluation


@pytest.fixture(autouse=True)
def clean(db):
    RiskEvaluation.objects.all().delete()
    FraudAlert.objects.all().delete()
    yield


@pytest.fixture
def user(django_user_model, db):
    return django_user_model.objects.create_user("al-user", email="al@t.io", password="x")


def _evaluation(user, decision, **kw):
    base = dict(
        operation_type="TRANSFER", actor=user,
        engine_mode=RiskEvaluation.EngineMode.ENFORCEMENT,
        decision=decision,
        triggered_rules=[{"rule_id": "R1", "version": 1, "score": 30}],
    )
    base.update(kw)
    return RiskEvaluation.objects.create(**base)


def test_review_creates_medium_block_creates_high(user):
    a1 = raise_alert(
        _evaluation(user, RiskEvaluation.Decision.REVIEW,
                    triggered_rules=[{"rule_id": "RA", "version": 1, "score": 30}])
    )
    a2 = raise_alert(
        _evaluation(user, RiskEvaluation.Decision.BLOCK,
                    triggered_rules=[{"rule_id": "RB", "version": 1, "score": 90}])
    )
    assert a1.severity == "MEDIUM" and a2.severity == "HIGH"
    assert a1.status == FraudAlert.Status.OPEN


def test_allow_never_creates_alert(user):
    assert raise_alert(_evaluation(user, RiskEvaluation.Decision.ALLOW)) is None


def test_identical_incidents_deduplicate(user):
    e1 = _evaluation(user, RiskEvaluation.Decision.REVIEW)
    e2 = _evaluation(user, RiskEvaluation.Decision.REVIEW)
    a1 = raise_alert(e1)
    a2 = raise_alert(e2)  # same op/customer/rules within window
    assert a1.pk == a2.pk  # one incident, one alert
    assert FraudAlert.objects.count() == 1


def test_different_rules_or_customer_create_separate_alerts(user, django_user_model):
    other = django_user_model.objects.create_user("al-other", email="alo@t.io", password="x")
    raise_alert(_evaluation(user, RiskEvaluation.Decision.REVIEW))
    raise_alert(_evaluation(other, RiskEvaluation.Decision.REVIEW))
    e3 = _evaluation(
        user, RiskEvaluation.Decision.REVIEW,
        triggered_rules=[{"rule_id": "R2", "version": 1, "score": 10}],
    )
    raise_alert(e3)
    assert FraudAlert.objects.count() == 3


def test_old_open_alert_outside_window_does_not_absorb(user):
    a = raise_alert(_evaluation(user, RiskEvaluation.Decision.REVIEW))
    FraudAlert.objects.filter(pk=a.pk).update(created_at=timezone.now() - timezone.timedelta(hours=2))
    raise_alert(_evaluation(user, RiskEvaluation.Decision.REVIEW))
    assert FraudAlert.objects.count() == 2


def test_acknowledge_then_close_lifecycle(user):
    a = raise_alert(_evaluation(user, RiskEvaluation.Decision.BLOCK))
    a.status = FraudAlert.Status.ACKNOWLEDGED
    a.save()
    a.status = FraudAlert.Status.CLOSED
    a.resolved_at = timezone.now()
    a.save()
    assert a.status == FraudAlert.Status.CLOSED and a.resolved_at is not None
