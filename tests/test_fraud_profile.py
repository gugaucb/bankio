"""Sensitive profile changes run through the risk engine; never fatal."""
import pytest
from django.urls import reverse

from apps.fraud.models import RiskEvaluation, RiskRule
from apps.audit.models import AuditLog


@pytest.fixture(autouse=True)
def clean(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield


@pytest.fixture
def customer(db, django_user_model, client):
    user = django_user_model.objects.create_user("prof-user", email="prof@t.io", password="old-pass-123")
    client.force_login(user)
    return user, client


def test_password_change_creates_evaluation(customer):
    user, client = customer
    r = client.post(
        reverse("app_security"),
        {"change_password": "", "old_password": "old-pass-123",
         "new_password1": "new-pass-456", "new_password2": "new-pass-456"},
    )
    assert r.status_code == 302
    ev = RiskEvaluation.objects.filter(operation_type="PASSWORD_CHANGE").latest("pk")
    assert ev.actor == user
    assert ev.engine_mode == RiskEvaluation.EngineMode.SHADOW
    assert AuditLog.objects.filter(actor=user, action="PASSWORD_CHANGED").exists()


def test_engine_crash_does_not_break_password_change(customer, monkeypatch):
    from apps.fraud import engine

    monkeypatch.setattr(engine, "evaluate_operation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    user, client = customer
    r = client.post(
        reverse("app_security"),
        {"change_password": "", "old_password": "old-pass-123",
         "new_password1": "new-pass-789", "new_password2": "new-pass-789"},
    )
    assert r.status_code == 302
    assert AuditLog.objects.filter(action="RISK_EVALUATION_ERROR").exists()
    user.refresh_from_db()
    assert user.check_password("new-pass-789")


def test_evaluate_profile_change_service_direct(django_user_model, db):
    from apps.fraud.profile_risk import evaluate_profile_change

    user = django_user_model.objects.create_user("svc-prof", email="svc@t.io", password="x")
    ev = evaluate_profile_change(user)
    assert ev.operation_type == "PROFILE_UPDATE"
    assert ev.status == RiskEvaluation.Status.COMPLETED
