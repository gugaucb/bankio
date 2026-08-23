"""Fraud console: RBAC-guarded internal views render correct data."""
import pytest
from django.urls import reverse

from apps.fraud.cases import open_case
from apps.fraud.models import FraudAlert, FraudCase, RiskEvaluation


@pytest.fixture(autouse=True)
def clean(db):
    FraudCase.objects.all().delete()
    FraudAlert.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield


@pytest.fixture
def client(client):
    return client


def _login(django_user_model, client, role):
    u = django_user_model.objects.create_user(
        f"console-{role.lower()}", email=f"c{role.lower()}@t.io", password="x", role=role,
    )
    client.force_login(u)
    return u


def test_customer_cannot_access_console(django_user_model, client, db):
    u = django_user_model.objects.create_user("plain-cust", email="pc@t.io", password="x")
    client.force_login(u)
    assert client.get(reverse("fraud:dashboard")).status_code == 403
    assert client.get(reverse("fraud:alert_queue")).status_code == 403


def test_anonymous_redirected(django_user_model, client, db):
    resp = client.get(reverse("fraud:dashboard"))
    assert resp.status_code in (301, 302)


def test_dashboard_shows_metrics(django_user_model, client, db):
    _login(django_user_model, client, "FRAUD_ANALYST")
    RiskEvaluation.objects.create(
        operation_type="TRANSFER",
        engine_mode=RiskEvaluation.EngineMode.ENFORCEMENT,
        decision=RiskEvaluation.Decision.BLOCK,
        risk_score=90, risk_level=RiskEvaluation.RiskLevel.CRITICAL,
        triggered_rules=[{"rule_id": "R9", "version": 1, "score": 90}],
    )
    FraudAlert.objects.create(alert_type="BLOCK:TRANSFER", severity="HIGH")
    resp = client.get(reverse("fraud:dashboard"))
    body = resp.content.decode()
    assert resp.status_code == 200
    for needle in ("Open Alerts", "Blocked", "R9"):
        assert needle in body


def test_alert_queue_filters_and_actions_visible(django_user_model, client, db):
    analyst = _login(django_user_model, client, "SENIOR_FRAUD_ANALYST")
    alert = FraudAlert.objects.create(customer=analyst, alert_type="REVIEW:TRANSFER", severity="MEDIUM")
    resp = client.get(reverse("fraud:alert_queue") + "?severity=HIGH")
    assert alert.alert_type not in resp.content.decode()
    resp = client.get(reverse("fraud:alert_queue"))
    assert alert.alert_type in resp.content.decode()


def test_case_flow_through_console(django_user_model, client, db):
    analyst = _login(django_user_model, client, "FRAUD_ANALYST")
    alert = FraudAlert.objects.create(customer=analyst, alert_type="BLOCK:TRANSFER", severity="HIGH")

    # analyst opens case from alert
    resp = client.post(reverse("fraud:open_case_from_alert", args=[alert.pk]))
    case = FraudCase.objects.latest("pk")
    assert resp.status_code == 302 and case.alerts.count() == 1

    # analyst cannot confirm fraud (needs senior+)
    resp = client.post(reverse("fraud:decide_case", args=[case.pk]),
                       {"status": "INVESTIGATING"})
    assert resp.status_code == 302

    senior = django_user_model.objects.create_user(
        "console-senior", email="cs@t.io", password="x", role="SENIOR_FRAUD_ANALYST")
    client.force_login(senior)
    resp = client.post(reverse("fraud:decide_case", args=[case.pk]),
                       {"status": "CONFIRMED_FRAUD", "reason": "verified with customer"})
    case.refresh_from_db()
    assert case.status == FraudCase.Status.CONFIRMED_FRAUD
    assert any(e.event_type.endswith("CONFIRMED_FRAUD") for e in case.events.all())
