"""Insider-risk correlation: explainable factors, super-linear points."""
import pytest

from apps.fraud.insider import MIN_FACTORS, correlate_insider_risk
from apps.fraud.models import RiskEvaluation


def _eval(user, **kw):
    defaults = dict(
        operation_type="MANAGER_RESTRICTION", actor=user, status=RiskEvaluation.Status.COMPLETED,
        decision=RiskEvaluation.Decision.ALLOW, engine_mode=RiskEvaluation.EngineMode.SHADOW,
    )
    defaults.update(kw)
    return RiskEvaluation.objects.create(**defaults)


@pytest.fixture
def manager(db, django_user_model):
    return django_user_model.objects.create_user("ins-mgr", email="ins@t.io", password="x", role="MANAGER")


@pytest.mark.django_db
def test_no_factors_below_thresholds(manager):
    _eval(manager)
    r = correlate_insider_risk(manager)
    assert r["factor_count"] == 0 and r["insider_points"] == 0
    assert "no insider" in r["explanation"]


@pytest.mark.django_db
def test_volume_alone_not_enough(manager):
    for _ in range(6):
        _eval(manager)
    r = correlate_insider_risk(manager)
    assert "high_operation_volume" in r["factors"]
    assert r["insider_points"] == 0  # single factor below MIN_FACTORS


@pytest.mark.django_db
def test_two_factors_correlate(manager):
    for _ in range(6):
        _eval(manager)
    _eval(manager, customer=manager, operation_type="ACCOUNT_OPENING")
    r = correlate_insider_risk(manager)
    assert r["factor_count"] >= MIN_FACTORS
    assert r["insider_points"] > 0


@pytest.mark.django_db
def test_negative_outcomes_count_as_factor(manager):
    for i in range(6):
        _eval(manager, decision=RiskEvaluation.Decision.BLOCK)
    r = correlate_insider_risk(manager)
    assert set(r["factors"]) == {"high_operation_volume", "repeated_negative_outcomes"}
    assert r["insider_points"] == 55  # 5 + 25 * 2
