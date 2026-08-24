"""FASE 4.4 S1 — Security Operations console: engine health (staff-only)."""
import pytest
from django.urls import reverse

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
