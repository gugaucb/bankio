"""Authentication risk: signals, LOGIN policy outcomes, no fabricated geography."""
import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.fraud import signals  # noqa: F401
from apps.fraud import signals_auth  # noqa: F401 (registration)
from apps.fraud.auth_risk import evaluate_login
from apps.fraud.context import RiskContext
from apps.fraud.engine import evaluate_operation
from apps.fraud.modes import effective_decision
from apps.fraud.policies import decide


@pytest.fixture(autouse=True)
def clean(db):
    from apps.fraud.models import RiskEvaluation

    RiskEvaluation.objects.all().delete()
    yield


@pytest.fixture
def user(django_user_model, db):
    return django_user_model.objects.create_user("auth-user", email="au@t.io", password="x")


def _ctx(user=None, ip=""):
    return RiskContext(operation_type="LOGIN", actor=user, ip=ip, timestamp=timezone.now())


def test_ip_change_signal_true_only_after_baseline(user, db):
    AuditLog.objects.create(actor=user, action="LOGIN", ip_address="10.0.0.1")
    out = signals.collect(_ctx(user, ip="10.0.0.1"), ["IP_DIFFERS_FROM_LAST_LOGIN"])
    assert out["IP_DIFFERS_FROM_LAST_LOGIN"] is False
    out = signals.collect(_ctx(user, ip="10.9.9.9"), ["IP_DIFFERS_FROM_LAST_LOGIN"])
    assert out["IP_DIFFERS_FROM_LAST_LOGIN"] is True
    # no baseline -> unknown, NOT "changed"
    fresh = django_user_model = None
    out = signals.collect(_ctx(ip=""), ["IP_DIFFERS_FROM_LAST_LOGIN"])
    assert out["IP_DIFFERS_FROM_LAST_LOGIN"] is None


def test_no_country_signal_is_explicit():
    """D-F04: geography unavailable — must not be silently faked."""
    assert "COUNTRY_CHANGE" not in signals.REGISTRY


def test_login_velocity_counts_success_and_failure(user, db):
    AuditLog.objects.create(actor=user, action="LOGIN", ip_address="10.0.0.2")
    AuditLog.objects.create(actor=user, action="LOGIN_FAILED", ip_address="10.0.0.2")
    out = signals.collect(_ctx(user), ["LOGIN_VELOCITY_15MIN"])
    assert out["LOGIN_VELOCITY_15MIN"] == 2


def test_login_policy_outcomes():
    assert decide("LOGIN", "LOW") == "ALLOW"
    assert decide("LOGIN", "MEDIUM") == "CHALLENGE"
    assert decide("LOGIN", "HIGH") == "CHALLENGE"
    assert decide("LOGIN", "CRITICAL") == "BLOCK"


def test_evaluate_login_records_and_shadow_does_not_interfere(db, user, settings):
    settings.FRAUD_MODE = None
    ev = evaluate_login(user, ip="10.5.5.5")
    ev.refresh_from_db()
    assert ev.operation_type == "LOGIN"
    assert ev.status == "COMPLETED"
    assert effective_decision(ev) in ("ALLOW", "CHALLENGE", "REVIEW", "BLOCK")
