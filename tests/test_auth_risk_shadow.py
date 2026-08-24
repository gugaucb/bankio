"""R1 — Shadow login wiring: evaluate_login participates in the real flow
without ever blocking (SHADOW). Existing auth behavior fully preserved."""
import pytest
from django.test import Client

from apps.audit.models import AuditLog
from apps.fraud.models import RiskEvaluation

PW = "Str0ng-pass!x"


@pytest.fixture(autouse=True)
def shadow_mode(settings):
    settings.FRAUD_MODE = "SHADOW"


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(
        "risk-alice", email="ra@t.io", password=PW, role="CUSTOMER")


def _c():
    return Client(HTTP_USER_AGENT="RiskShadow/1")


# ------------------------------------------------------------- real wiring

@pytest.mark.django_db
def test_real_login_produces_risk_evaluation(client, alice):
    r = client.post("/login/", {"username": "risk-alice", "password": PW},
                    HTTP_USER_AGENT="ShadowE2E/1")
    assert r.status_code == 302 and "/app" in r["Location"]
    ev = RiskEvaluation.objects.get(operation_type="LOGIN", actor=alice,
                                    status="COMPLETED")
    assert ev.engine_mode == "SHADOW"
    assert ev.decision in ("ALLOW", "CHALLENGE", "REVIEW", "BLOCK")   # recorded, not enforced


@pytest.mark.django_db
def test_nonexistent_user_never_evaluated(client, db):
    """Anti-enumeration: no user → no evaluation, same 'invalid credentials'."""
    r = client.post("/login/", {"username": "who-is-this", "password": PW})
    assert r.status_code == 200          # form re-render, generic error
    assert not RiskEvaluation.objects.filter(operation_type="LOGIN").exists()
    assert not AuditLog.objects.filter(action__in=("LOGIN", "LOGIN_FAILED"),
                                       actor__username="who-is-this").exists()


@pytest.mark.django_db
def test_failed_password_records_login_failed_but_no_evaluation(client, alice):
    r = client.post("/login/", {"username": "risk-alice", "password": "wrong"})
    assert r.status_code == 200
    assert AuditLog.objects.filter(action="LOGIN_FAILED", actor=alice).exists()
    # evaluation only for authenticated identities (no signal basis without a user session)
    assert not RiskEvaluation.objects.filter(
        operation_type="LOGIN", status="COMPLETED").exists()


# ------------------------------------------------------- behavior preserved

@pytest.mark.django_db
def test_lockout_behavior_unchanged(alice):
    from apps.identity.services import LoginLocked, attempt_login
    from django.test import RequestFactory

    rf = RequestFactory()
    for i in range(5):
        attempt_login("risk-alice", "bad", rf.post("/login/"))
    with pytest.raises(LoginLocked):
        attempt_login("risk-alice", "wrong", rf.post("/login/"))
    # correct password still locked out — risk never bypasses lockout
    with pytest.raises(LoginLocked):
        attempt_login("risk-alice", PW, rf.post("/login/"))


@pytest.mark.django_db
def test_device_still_registered_and_audited(client, alice):
    from apps.identity.models import Device

    client.post("/login/", {"username": "risk-alice", "password": PW},
                HTTP_USER_AGENT="DevReg/1")
    assert Device.objects.filter(user=alice, name="DevReg/1").exists()
    assert AuditLog.objects.filter(actor=alice, action="LOGIN").exists()


# ------------------------------------------------------------- shadow only

@pytest.mark.django_db
def test_engine_error_does_not_break_shadow_login(alice, monkeypatch):
    """Engine failure: FAILED snapshot + audit recorded; SHADOW still logs in."""
    from django.test import RequestFactory

    def boom(*a, **k):
        raise RuntimeError("engine down")

    # fail mid-pipeline (after the evaluation snapshot is created)
    monkeypatch.setattr("apps.fraud.rules.evaluate_rules", boom)
    from apps.identity.services import attempt_login

    rf = RequestFactory()
    user, needs_otp = attempt_login("risk-alice", PW,
                                    rf.post("/login/", HTTP_USER_AGENT="X/1"))
    assert user is not None and needs_otp is False     # shadow fail-soft for auth UX
    ev = RiskEvaluation.objects.get(operation_type="LOGIN", actor=alice, status="FAILED")
    assert AuditLog.objects.filter(action="RISK_EVALUATION_ERROR").exists()


@pytest.mark.django_db
def test_critical_decision_in_shadow_never_blocks(client, alice, django_user_model):
    """Even a CRITICAL/BLOCK outcome is record-only under SHADOW."""
    from apps.fraud import modes
    from apps.fraud.models import RiskRule
    from django.contrib.auth import get_user_model

    mgr = get_user_model().objects.create_user("shadow-mgr", email="sm@t.io",
                                               password="x", role="FRAUD_MANAGER")
    RiskRule.objects.create(rule_id="LOGIN-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True,
                            operation_types=["LOGIN"])
    modes.set_mode(RiskEvaluation.EngineMode.SHADOW, actor=mgr)

    u = django_user_model.objects.get(username="risk-alice")
    client = _c()
    client.force_login(u)   # ensure exists
    r = Client(HTTP_USER_AGENT="CritLogin/1").post(
        "/login/", {"username": "risk-alice", "password": PW})
    assert r.status_code == 302 and "/app" in r["Location"]   # logged in anyway
    ev = RiskEvaluation.objects.filter(operation_type="LOGIN").latest("pk")
    assert ev.decision == "BLOCK"      # evidence says BLOCK…
    assert ev.engine_mode == "SHADOW"  # …mode says record-only
