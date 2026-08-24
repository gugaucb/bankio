"""R5 — PASSWORD_CHANGE/PROFILE_UPDATE policies + enforcement wiring."""
import re

import pytest
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.fraud.models import RiskEvaluation, RiskRule
from apps.fraud.modes import set_mode


@pytest.fixture(autouse=True)
def clean(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield


@pytest.fixture
def customer(db, django_user_model, client):
    user = django_user_model.objects.create_user(
        "r5-user", email="r5@t.io", password="old-pass-123")
    client.force_login(user)
    return user, client


def _pw(client, new="new-pass-456", **extra):
    return client.post(
        reverse("app_security"),
        {"change_password": "", "old_password": "old-pass-123",
         "new_password1": new, "new_password2": new, **extra},
    )


def _rule(score):
    return RiskRule.objects.create(
        rule_id="R5-RULE", name="n", score=score,
        lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True,
        operation_types=["PASSWORD_CHANGE"])


# ------------------------------------------------------------------ shadow

@pytest.mark.django_db
def test_shadow_change_ignores_smuggled_risk_fields(customer):
    user, client = customer
    r = _pw(client, risk_code="000000", decision="BLOCK")   # must be ignored
    assert r.status_code == 302
    user.refresh_from_db()
    assert user.check_password("new-pass-456")


# -------------------------------------------------------------- challenge

@pytest.mark.django_db
def test_high_risk_requires_step_up_before_applying(customer, caplog):
    set_mode("CHALLENGE_ONLY")
    _rule(70)                                   # HIGH → CHALLENGE
    user, client = customer
    r = _pw(client)
    assert r.status_code == 200                 # not applied yet; code requested
    assert b"risk_code" in r.content
    user.refresh_from_db()
    assert not user.check_password("new-pass-456")   # old password intact
    match = re.search(r"password change code for \S+: (\d{6})", caplog.text)
    assert match
    # resubmit with the code (form data must be resent)
    r2 = client.post(reverse("app_security"), {
        "change_password": "", "old_password": "old-pass-123",
        "new_password1": "new-pass-456", "new_password2": "new-pass-456",
        "risk_code": match.group(1)})
    assert r2.status_code == 302
    user.refresh_from_db()
    assert user.check_password("new-pass-456")


@pytest.mark.django_db
def test_wrong_challenge_code_leaves_password_unchanged(customer, caplog):
    set_mode("CHALLENGE_ONLY")
    _rule(70)
    user, client = customer
    _pw(client)                                 # issue challenge
    r = _pw(client, risk_code="999999")
    assert b"Invalid or expired" in r.content or r.status_code == 200
    user.refresh_from_db()
    assert not user.check_password("new-pass-456")
    assert not AuditLog.objects.filter(actor=user, action="PASSWORD_CHANGED").exists()


# ------------------------------------------------------------------ block

@pytest.mark.django_db
def test_engine_failure_fail_closed_under_enforcement(customer, monkeypatch):
    from apps.fraud import engine

    set_mode("ENFORCEMENT")
    monkeypatch.setattr(engine, "evaluate_operation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    user, client = customer
    r = _pw(client)
    assert r.status_code == 302                 # redirect with error message
    user.refresh_from_db()
    assert not user.check_password("new-pass-456")   # fail-closed: NOT changed
    assert AuditLog.objects.filter(action="PASSWORD_CHANGE_BLOCKED").exists()
    assert AuditLog.objects.filter(action="RISK_EVALUATION_ERROR").exists()


@pytest.mark.django_db
def test_engine_failure_in_shadow_still_applies(customer, monkeypatch):
    from apps.fraud import engine

    monkeypatch.setattr(engine, "evaluate_operation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    user, client = customer
    assert _pw(client).status_code == 302
    user.refresh_from_db()
    assert user.check_password("new-pass-456")   # observational mode unchanged


def test_explicit_password_change_policy_exists():
    from apps.fraud.models import RiskEvaluation
    from apps.fraud.policies import POLICIES

    pol = POLICIES["PASSWORD_CHANGE"]
    assert set(pol) == set(RiskEvaluation.RiskLevel.values)   # every level mapped
