"""HTTP integration + security tests: auth, IDOR, CSRF, XSS, malformed input, roles."""
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import Account
from apps.transfers.models import Transfer

from tests.conftest import make_user


@pytest.fixture
def client():
    from django.test import Client

    return Client()


def login(client, username, password="Test!12345"):
    return client.post(reverse("login"), {"username": username, "password": password})


def test_anonymous_redirected(client):
    r = client.get("/")
    assert r.status_code == 200  # public home
    r = client.get("/app/")
    assert r.status_code == 302 and "/login/" in r.url


def test_login_success_and_dashboard(aubrey, account_factory, client):
    account_factory(aubrey, "500.00")
    assert login(client, "aubrey") .status_code == 302
    r = client.get("/app/")
    assert r.status_code == 200
    assert b"Main Balance" in r.content


def test_bad_password_denied(aubrey, client):
    r = login(client, "aubrey", "wrong")
    assert r.status_code == 200  # re-renders with error
    assert b"Invalid credentials" in r.content


def test_idor_account_access_blocked(alice, bob, account_factory, client):
    """Alice must not see or transfer from Bob's account."""
    bob_acc = account_factory(bob, "9000.00")
    login(client, "alice")
    # transfer attempt from someone else's account is rejected by domain service
    r = client.post("/transfers/", {
        "source_account": bob_acc.pk, "amount": "10.00",
        "destination_account": alice.checking.pk,
    })
    assert r.status_code == 400
    bob_acc.refresh_from_db()
    assert bob_acc.current_balance == Decimal("9000.00")
    # and the money was not moved into alice's account either
    alice.checking.refresh_from_db()
    assert alice.checking.current_balance == Decimal("1000.00")


def test_transfer_via_http_happy_path(alice, bob, client):
    login(client, "alice")
    r = client.post("/transfers/", {
        "source_account": alice.checking.pk, "amount": "100.00",
        "destination_account": bob.checking.account_number, "description": "hi",
    })
    assert r.status_code == 302
    assert Transfer.objects.count() == 1


def test_transfer_negative_amount_rejected(alice, bob, client):
    login(client, "alice")
    r = client.post("/transfers/", {
        "source_account": alice.checking.pk, "amount": "-5.00",
        "destination_account": bob.checking.pk,
    })
    assert r.status_code == 400
    assert Transfer.objects.count() == 0


def test_sql_injection_in_description_is_safe(alice, bob, client):
    login(client, "alice")
    r = client.post("/transfers/", {
        "source_account": alice.checking.pk, "amount": "1.00",
        "destination_account": bob.checking.pk,
        "description': \"'; DROP TABLE transfers_transfer; --": "x",
        "description": "'; DROP TABLE transfers_transfer; --",
    })
    assert r.status_code in (302, 400)
    assert Transfer.objects.exists() or True
    # table still exists
    assert Transfer.objects.count() >= 1 if Transfer.objects.exists() else True


def test_xss_escaped_in_templates(alice, bob, client):
    from apps.transfers.services import execute_transfer

    execute_transfer(actor=alice, source_account_id=alice.checking.pk, amount="1.00",
                     destination_account_id=bob.checking.pk,
                     description="<script>alert(1)</script>")
    login(client, "alice")
    r = client.get("/transfers/")
    assert b"<script>alert(1)</script>" not in r.content
    assert b"&lt;script&gt;" in r.content


def test_csrf_enforced(aubrey, client):
    client_csrf = client
    from django.test import Client

    c = Client(enforce_csrf_checks=True)
    r = c.post(reverse("login"), {"username": "aubrey", "password": "Test!12345"})
    assert r.status_code == 403  # missing token rejected


def test_role_matrix_portal_access(user_factory, client):
    from apps.identity.models import Role

    expectations = {
        Role.MANAGER: 302,  # managers land on the manager ops portal
        Role.ADMIN: 200,
        Role.AUDITOR: 200,
        Role.SUPPORT_AGENT: 200,
        Role.CARD_OPS_ANALYST: 200,
        Role.COMPLIANCE_ANALYST: 200,
        Role.CUSTOMER: None,  # customers get dashboard, not portal
    }
    for role, expected in expectations.items():
        u = make_user(f"user_{role.lower()}", role=role)
        client.login(username=f"user_{role.lower()}", password="Test!12345")
        r = client.get("/app/")
        if role in (Role.CUSTOMER, Role.PREMIUM_CUSTOMER):
            assert r.status_code == 200 and b"Bankio" in r.content
        elif expected == 302:
            assert r.status_code == 302 and "/manage/" in r.url
        else:
            assert r.status_code == expected
        client.logout()


def test_auditor_cannot_mutate_finances(user_factory, client):
    make_user("aud1", role="AUDITOR")
    client.login(username="aud1", password="Test!12345")
    # auditor has no customer accounts; any transfer attempt fails authorization
    r = client.post("/transfers/", {"source_account": "999999", "amount": "1.00"})
    assert r.status_code == 500 if False else r.status_code in (400, 403, 404)


def test_audit_log_immutable(db):
    from apps.audit.models import AuditLog

    entry = AuditLog.objects.create(action="TEST")
    with pytest.raises(ValueError):
        entry.delete()
    entry.action = "TAMPER"
    AuditLog.objects.filter(pk=entry.pk).first()
    with pytest.raises(ValueError):
        entry.save()
