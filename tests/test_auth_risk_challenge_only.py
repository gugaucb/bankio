"""R4 — CHALLENGE_ONLY rollout + fail-closed LOGIN (SHADOW non-interference)."""
import pytest
from django.test import Client, RequestFactory

from apps.fraud.models import RiskRule
from apps.fraud.modes import set_mode
from apps.identity.services import (
    LoginLocked,
    LoginRiskBlocked,
    attempt_login,
    verify_otp,
)

PW = "Str0ng-pass!x"


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(
        "co-alice", email="co@t.io", password=PW, role="CUSTOMER")


def _rule(score, rule_id="CO-RULE"):
    return RiskRule.objects.create(
        rule_id=rule_id, name="n", score=score,
        lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True,
        operation_types=["LOGIN"])


def _login(username="co-alice", password=PW, ua="CO/1", **extra):
    rf = RequestFactory()
    req = rf.post("/login/", HTTP_USER_AGENT=ua, **extra)
    return attempt_login(username, password, req)


# ------------------------------------------------------------------ shadow

@pytest.mark.django_db
def test_shadow_mode_never_interferes(alice):
    _rule(100)                                   # would be CRITICAL/BLOCK
    user, needs_otp = _login()
    assert user.pk == alice.pk and needs_otp is False  # SHADOW: straight through


# --------------------------------------------------------- challenge_only

@pytest.mark.django_db
def test_low_risk_logs_in_directly_under_challenge_only(alice):
    set_mode("CHALLENGE_ONLY")
    user, needs_otp = _login()                   # no rules → LOW → ALLOW
    assert user.pk == alice.pk and needs_otp is False


@pytest.mark.django_db
@pytest.mark.parametrize("score", [45, 70])      # MEDIUM and HIGH → CHALLENGE
def test_medium_high_risk_requires_otp_step_up(alice, score):
    set_mode("CHALLENGE_ONLY")
    _rule(score)
    user, needs_otp = _login(ua=f"UA-{score}/1")
    assert user.pk == alice.pk and needs_otp is True   # same OTP infra as MFA login


@pytest.mark.django_db
def test_critical_downgraded_to_challenge_under_challenge_only(alice):
    set_mode("CHALLENGE_ONLY")
    _rule(100)
    user, needs_otp = _login()
    assert needs_otp is True                     # BLOCK never fires in this mode


@pytest.mark.django_db
def test_wrong_then_right_otp_completes_challenge(alice, caplog):
    import re

    set_mode("CHALLENGE_ONLY")
    _rule(45)
    with caplog.at_level("INFO", logger="bankio.challenge"):
        user, needs_otp = _login()
    assert needs_otp is True
    match = re.search(r"risk challenge code for \S+: (\d{6})", caplog.text)
    assert match                                  # challenge was really issued
    code = match.group(1)
    assert verify_otp(user, "000000") is False   # wrong code fails closed
    assert verify_otp(user, code) is True        # right code completes step-up


@pytest.mark.django_db
def test_mfa_user_still_gets_existing_flow(alice, settings):
    settings.FRAUD_MODE = "CHALLENGE_ONLY"
    alice.mfa_enabled = True
    alice.save(update_fields=["mfa_enabled"])
    user, needs_otp = _login()
    assert needs_otp is True                     # unchanged MFA behavior


# ------------------------------------------------------------- enforcement

@pytest.mark.django_db
def test_block_only_fires_under_enforcement(alice):
    set_mode("ENFORCEMENT")
    _rule(100)                                   # CRITICAL → BLOCK
    with pytest.raises(LoginRiskBlocked):
        _login()


@pytest.mark.django_db
def test_blocked_login_never_creates_session_or_pending_otp(alice):
    set_mode("ENFORCEMENT")
    _rule(100)
    c = Client(HTTP_USER_AGENT="Blocked/1")
    resp = c.post("/login/", {"username": "co-alice", "password": PW})
    assert resp.status_code == 200               # form re-render, generic error
    assert b"Unable to sign in" in resp.content
    assert "pending_otp_user" not in c.session
    assert not c.session.session_key or "_auth_user_id" not in c.session


# --------------------------------------------------------------- fail-safe

@pytest.mark.django_db
def test_engine_failure_is_fail_closed_outside_shadow(alice, monkeypatch):
    set_mode("CHALLENGE_ONLY")

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("apps.fraud.rules.evaluate_rules", boom)
    with pytest.raises(LoginRiskBlocked):        # never a silent ALLOW
        _login()


@pytest.mark.django_db
def test_engine_failure_in_shadow_still_allows(alice, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("apps.fraud.rules.evaluate_rules", boom)
    user, _ = _login()                           # observational mode: proceed
    assert user.pk == alice.pk


# ------------------------------------------------------------------ abuse

@pytest.mark.django_db
def test_client_cannot_smuggle_decision(alice):
    """POST fields cannot influence the risk outcome."""
    set_mode("ENFORCEMENT")
    _rule(100)
    for smuggled in ({"decision": "ALLOW"}, {"risk_level": "LOW"},
                     {"effective_decision": "ALLOW"}):
        with pytest.raises(LoginRiskBlocked):
            _login(**smuggled)


@pytest.mark.django_db
def test_lockout_precedence_unchanged(alice):
    from datetime import timedelta

    from django.utils import timezone

    set_mode("ENFORCEMENT")
    _rule(0)                                     # even a permissive ruleset
    alice.locked_until = timezone.now() + timedelta(minutes=10)
    alice.save(update_fields=["locked_until"])
    with pytest.raises(LoginLocked):
        _login()
