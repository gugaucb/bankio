"""FASE 4.4 S1 — Security Operations console: engine health (staff-only)."""
import pytest
from django.urls import reverse

from apps.audit.models import AuditLog

PW = "Str0ng-pass!x"


def _user(django_user_model, username, role, superuser=False):
    return django_user_model.objects.create_user(
        username, email=f"{username}@t.io", password=PW, role=role,
        is_superuser=superuser)


@pytest.fixture
def admin(db, django_user_model, client):
    u = _user(django_user_model, "sec-admin", "ADMIN")
    client.force_login(u)
    return u, client


@pytest.fixture
def auditor(db, django_user_model, client):
    u = _user(django_user_model, "sec-auditor", "AUDITOR")
    client.force_login(u)
    return u, client


@pytest.fixture
def fraud_manager(db, django_user_model, client):
    u = _user(django_user_model, "sec-fm", "FRAUD_MANAGER")
    client.force_login(u)
    return u, client


@pytest.fixture
def customer(db, django_user_model, client):
    u = _user(django_user_model, "sec-cust", "CUSTOMER")
    client.force_login(u)
    return u, client


# ------------------------------------------------------------- access model

@pytest.mark.django_db
@pytest.mark.parametrize("fixture_name", ["admin", "auditor", "fraud_manager"])
def test_staff_roles_reach_health(fixture_name, request):
    _, client = request.getfixturevalue(fixture_name)
    r = client.get(reverse("fraud:secops_health"))
    assert r.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("fixture_name", ["admin", "auditor", "fraud_manager"])
def test_superuser_reaches_health(django_user_model, fixture_name, request):
    _user(django_user_model, f"super-{fixture_name}", "CUSTOMER", superuser=True)
    from django.test import Client as C

    c = C()
    c.force_login(_user(django_user_model, "su", "CUSTOMER", superuser=True))
    assert c.get(reverse("fraud:secops_health")).status_code == 200


@pytest.mark.django_db
def test_customer_and_anonymous_denied(customer):
    from django.test import Client as C

    assert C().get(reverse("fraud:secops_health")).status_code == 302  # anonymous → login
    cust_client = customer[1]
    assert cust_client.get(reverse("fraud:secops_health")).status_code == 403


# ------------------------------------------------------------------- health

@pytest.mark.django_db
def test_health_renders_metrics(admin):
    _, client = admin
    r = client.get(reverse("fraud:secops_health"))
    assert r.status_code == 200
    content = r.content.decode()
    assert "Engine Health" in content
    assert "Evaluations" in content and "Engine errors" in content


# ------------------------------------------------------- S2: mode control

@pytest.mark.django_db
def test_mode_page_visible_to_all_secops_roles(auditor):
    _, client = auditor
    r = client.get(reverse("fraud:secops_mode"))
    assert r.status_code == 200
    assert b"Read-only" in r.content          # auditor cannot change


@pytest.mark.django_db
def test_auditor_cannot_post_mode_change(auditor):
    from apps.fraud.models import FraudEngineSetting

    _, client = auditor
    assert client.post(reverse("fraud:secops_mode"),
                       {"mode": "CHALLENGE_ONLY"}).status_code == 403
    assert not FraudEngineSetting.objects.filter(key="FRAUD_MODE").exists()


@pytest.mark.django_db
def test_customer_cannot_change_mode(customer):
    _, client = customer
    assert client.post(reverse("fraud:secops_mode"),
                       {"mode": "ENFORCEMENT"}).status_code == 403


@pytest.mark.django_db
def test_fraud_manager_changes_mode_with_audit(fraud_manager):
    from apps.fraud.modes import get_mode

    user, client = fraud_manager
    r = client.post(reverse("fraud:secops_mode"), {"mode": "CHALLENGE_ONLY"})
    assert r.status_code == 302
    assert get_mode() == "CHALLENGE_ONLY"
    log = AuditLog.objects.filter(action="FRAUD_MODE_CHANGED").latest("pk")
    assert log.metadata["to"] == "CHALLENGE_ONLY"


@pytest.mark.django_db
def test_unknown_mode_rejected_with_message(fraud_manager):
    from apps.fraud.modes import get_mode

    _, client = fraud_manager
    r = client.post(reverse("fraud:secops_mode"), {"mode": "FULL_POWER"},
                    follow=True)
    assert b"Unknown mode" in r.content
    assert get_mode() != "FULL_POWER"
