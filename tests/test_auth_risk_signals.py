"""R2 — Auth signals + ATO correlation wired to the real login (SHADOW)."""
import pytest
from django.test import Client

from apps.audit.models import AuditLog
from apps.fraud.models import RiskEvaluation, RiskRule, RiskSignal

PW = "Str0ng-pass!x"


@pytest.fixture(autouse=True)
def shadow(settings):
    settings.FRAUD_MODE = "SHADOW"


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(
        "sig-alice", email="sa@t.io", password=PW, role="CUSTOMER")


def _login(client, username="sig-alice", pw=PW, **extra):
    return client.post("/login/", {"username": username, "password": pw}, **extra)


def _signal(evaluation, sid):
    return RiskSignal.objects.filter(evaluation=evaluation, signal_id=sid).first()


# ------------------------------------------------------- signals in real login

@pytest.mark.django_db
def test_ip_change_signal_true_on_new_ip(client, alice):
    r1 = _login(client, REMOTE_ADDR="10.0.0.1")
    assert r1.status_code == 302
    client.logout()
    ev = RiskEvaluation.objects.filter(operation_type="LOGIN").latest("pk")
    assert _signal(ev, "IP_DIFFERS_FROM_LAST_LOGIN") is not None

    r2 = Client().post("/login/", {"username": "sig-alice", "password": PW},
                       REMOTE_ADDR="10.0.0.99")
    assert r2.status_code == 302
    ev2 = RiskEvaluation.objects.filter(operation_type="LOGIN").latest("pk")
    assert _signal(ev2, "IP_DIFFERS_FROM_LAST_LOGIN").value is True


@pytest.mark.django_db
def test_login_velocity_signal_counts_history(client, alice):
    for i in range(3):
        c = Client()
        _login(c)
        c.logout()
    ev = RiskEvaluation.objects.filter(operation_type="LOGIN").latest("pk")
    assert (_signal(ev, "LOGIN_VELOCITY_15MIN").value or 0) >= 2


@pytest.mark.django_db
def test_mfa_failure_signal_has_real_producer(client, alice, django_user_model):
    """Wrong OTP attempts now record LOGIN_MFA(otp_failure=true) and feed the signal."""
    from apps.identity.services import generate_otp

    u = django_user_model.objects.get(username="sig-alice")
    u.mfa_enabled = True
    u.save(update_fields=["mfa_enabled"])
    code = generate_otp(u)

    client.post("/login/", {"username": "sig-alice", "password": PW})
    # two wrong OTP attempts
    client.post("/otp/", {"code": "000000"})
    client.post("/otp/", {"code": "111111"})
    assert AuditLog.objects.filter(actor=u, action="LOGIN_MFA",
                                   metadata__otp_failure=True).count() == 2

    from apps.fraud.auth_risk import evaluate_login

    ev = evaluate_login(u)
    assert _signal(ev, "MFA_FAILURE_COUNT_24H").value == 2


@pytest.mark.django_db
def test_new_device_signal_and_trusted_semantics(client, alice):
    """trusted=False means 'not yet trusted', exercised via the real flow."""
    from apps.identity.models import Device

    r = _login(Client(HTTP_USER_AGENT="BrandNew/1"))
    assert r.status_code == 302
    dev = Device.objects.get(user=alice, name="BrandNew/1")
    assert dev.trusted is False            # born untrusted — not an attack verdict
    ev = RiskEvaluation.objects.filter(operation_type="LOGIN").latest("pk")
    assert _signal(ev, "NEW_DEVICE").value is True

    dev.trusted = True                     # owner opts in via Security Central
    dev.save(update_fields=["trusted"])
    client.logout()
    _login(Client(HTTP_USER_AGENT="BrandNew/1"))
    ev2 = RiskEvaluation.objects.filter(operation_type="LOGIN").latest("pk")
    assert _signal(ev2, "NEW_DEVICE").value is False


# ------------------------------------------------------------------- ATO bridge

@pytest.mark.django_db
def test_ato_signal_registered_and_scores(alice):
    from apps.fraud.auth_risk import evaluate_login
    from apps.fraud.signals import REGISTRY

    assert "ATO_CORRELATION_POINTS" in REGISTRY
    # two distinct factors in window: password change + failed logins
    AuditLog.objects.create(actor=alice, action="PASSWORD_CHANGED")
    for _ in range(3):
        AuditLog.objects.create(actor=alice, action="LOGIN_FAILED")
    ev = evaluate_login(alice)
    val = _signal(ev, "ATO_CORRELATION_POINTS").value
    assert val >= 30                       # MIN_FACTORS met → super-linear points


@pytest.mark.django_db
def test_ato_rule_influences_score_not_view(client, alice):
    """ATO enters as signal → DB rule → score/decision; nothing hardcoded."""
    from django.contrib.auth import get_user_model

    RiskRule.objects.create(
        rule_id="LOGIN-ATO", name="ATO correlation", score=60,
        lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True,
        operation_types=["LOGIN"],
        conditions=[{"signal": "ATO_CORRELATION_POINTS", "op": "ge", "value": 30}],
    )
    AuditLog.objects.create(actor=alice, action="PASSWORD_CHANGED")
    for _ in range(3):
        AuditLog.objects.create(actor=alice, action="LOGIN_FAILED")   # threshold: >=3

    mgr = get_user_model().objects.create_user("sig-mgr", email="m@t.io",
                                               password="x", role="FRAUD_MANAGER")
    from apps.fraud import modes

    modes.set_mode(RiskEvaluation.EngineMode.SHADOW, actor=mgr)

    r = _login(Client(HTTP_USER_AGENT="AtoLogin/1"))
    assert r.status_code == 302 and "/app" in r["Location"]   # shadow: never blocks
    ev = RiskEvaluation.objects.filter(operation_type="LOGIN").latest("pk")
    triggered_ids = [t["rule_id"] for t in ev.triggered_rules]
    assert "LOGIN-ATO" in triggered_ids
    assert ev.risk_score >= 60
    # decision came from the LOGIN policy mapping of the resulting level,
    # not from any hardcoded branch in the view/service layer
    assert ev.decision == dict(
        LOW="ALLOW", MEDIUM="CHALLENGE", HIGH="CHALLENGE", CRITICAL="BLOCK")[ev.risk_level]


@pytest.mark.django_db
def test_failing_signal_isolated_never_breaks_login(client, alice, monkeypatch):
    from apps.fraud.signals import REGISTRY

    def boom(ctx, user=None):
        raise RuntimeError("signal down")

    monkeypatch.setitem(REGISTRY, "IP_DIFFERS_FROM_LAST_LOGIN", boom)
    r = _login(client)
    assert r.status_code == 302
    ev = RiskEvaluation.objects.filter(operation_type="LOGIN").latest("pk")
    assert "__error__" in _signal(ev, "IP_DIFFERS_FROM_LAST_LOGIN").value
