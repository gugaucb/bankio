"""ATO correlation: sequences outweigh isolated events; explainable output."""
import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.fraud.ato import correlate_account_takeover
from apps.fraud.models import RiskEvaluation


@pytest.fixture(autouse=True)
def clean(db):
    RiskEvaluation.objects.all().delete()
    AuditLog.objects.filter(action__in=["PASSWORD_CHANGED", "LOGIN_FAILED"]).delete()
    yield


@pytest.fixture
def user(django_user_model, db):
    return django_user_model.objects.create_user("ato-user", email="ato@t.io", password="x")


def _ev(user, **signal_values):
    return RiskEvaluation.objects.create(
        operation_type="TRANSFER", actor=user,
        engine_mode=RiskEvaluation.EngineMode.SHADOW,
        decision=RiskEvaluation.Decision.ALLOW,
        risk_score=0, risk_level=RiskEvaluation.RiskLevel.LOW,
        signal_values=signal_values, status=RiskEvaluation.Status.COMPLETED,
        triggered_rules=[],
    )


def test_isolated_events_do_not_trigger_correlation(user):
    AuditLog.objects.create(actor=user, action="PASSWORD_CHANGED")
    r = correlate_account_takeover(user)
    assert r["factor_count"] == 1 and r["ato_points"] == 0  # below MIN_FACTORS


def test_sequence_escalates_super_linearly(user):
    # one factor
    AuditLog.objects.create(actor=user, action="PASSWORD_CHANGED")
    for _ in range(3):
        AuditLog.objects.create(actor=user, action="LOGIN_FAILED", ip_address="1.2.3.4")
    r2 = correlate_account_takeover(user)
    assert r2["factor_count"] == 2 and r2["ato_points"] == 55

    # add new device + new beneficiary -> 4 factors
    _ev(user, NEW_DEVICE=True)
    r3 = correlate_account_takeover(user)
    assert r3["factor_count"] == 3

    _ev(user, BENEFICIARY_IS_NEW=True)
    r4 = correlate_account_takeover(user)
    assert r4["factor_count"] == 4
    assert r4["ato_points"] > r3["ato_points"] > r2["ato_points"]
    assert "new_device" in r4["factors"] and "new_beneficiary" in r4["factors"]
    assert "4 correlated ATO factor" in r4["explanation"]


def test_stale_factors_outside_window_ignored(user):
    AuditLog.objects.create(actor=user, action="PASSWORD_CHANGED")
    old = AuditLog.objects.create(actor=user, action="PASSWORD_CHANGED")
    AuditLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timezone.timedelta(hours=72))
    r = correlate_account_takeover(user, window_hours=48)
    assert r["factor_count"] == 1


def test_clean_customer_zero(user):
    r = correlate_account_takeover(user)
    assert r["factor_count"] == 0 and r["ato_points"] == 0
