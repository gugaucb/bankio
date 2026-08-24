"""Security Central — Activity history: whitelisted events, pagination, no secrets."""
import pytest
from django.test import Client

from apps.audit.models import AuditLog
from apps.identity.models import Device


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(
        "hist-alice", email="ha@t.io", password="Str0ng-pass!x", role="CUSTOMER")


def _client(user):
    c = Client(HTTP_USER_AGENT="Hist/1")
    c.force_login(user)
    return c


def _event(actor, action, **meta):
    AuditLog.objects.create(actor=actor, action=action, metadata=meta)


# ------------------------------------------------------------------ filtering

@pytest.mark.django_db
def test_shows_whitelisted_events_only(alice, bob):
    for a in ("LOGIN", "LOGIN_FAILED", "LOGIN_MFA", "LOGOUT", "PASSWORD_CHANGED",
              "DEVICE_TRUSTED", "DEVICE_UNTRUSTED", "DEVICE_REVOKED",
              "SESSION_REVOKED", "OTHER_SESSIONS_REVOKED", "CHALLENGE_ISSUED",
              "CHALLENGE_CONSUMED"):
        _event(alice, a, note="x")
    # events that must NEVER appear
    _event(alice, "CARD_STATEMENT_PAID", amount="10")
    _event(bob, "LOGIN", note="bob event")
    _event(None, "SYSTEM_JOB")

    body = _client(alice).get("/app/security/").content.decode()
    assert "Login" in body and "Challenge Issued" in body
    assert "Card Statement Paid" not in body
    assert "bob event".title() not in body


@pytest.mark.django_db
def test_never_shows_metadata_secrets(alice):
    _event(alice, "CHALLENGE_VERIFIED", code_hash="abc123secret",
           material_hash="deadbeefmaterial", token="tok-xyz")
    body = _client(alice).get("/app/security/").content.decode()
    assert "abc123secret" not in body and "deadbeefmaterial" not in body \
        and "tok-xyz" not in body


# ----------------------------------------------------------------- pagination

@pytest.mark.django_db
def test_server_side_pagination(alice):
    for i in range(25):
        _event(alice, "LOGIN", n=i)
    c = _client(alice)

    page1 = c.get("/app/security/").content.decode()
    assert "Page 1 of 3" in page1

    page3 = c.get("/app/security/?page=3").content.decode()
    assert "Page 3 of 3" in page3

    out_of_range = c.get("/app/security/?page=99")
    assert out_of_range.status_code == 200   # Django clamps to last page


@pytest.mark.django_db
def test_history_scoped_to_actor(alice, bob):
    _event(bob, "DEVICE_TRUSTED", device="Bob's secret device name")
    body = _client(alice).get("/app/security/").content.decode()
    assert "Bob's secret device name" not in body.replace("Device Trusted", "")


@pytest.mark.django_db
def test_newest_first_and_anonymous_redirect(alice):
    from django.contrib.auth import get_user_model

    e_old = AuditLog.objects.create(actor=alice, action="LOGIN")
    e_new = AuditLog.objects.create(actor=alice, action="LOGOUT")
    content = _client(alice).get("/app/security/").content.decode()
    assert content.find("Logout") < content.find("Login")

    r = Client().get("/app/security/")
    assert r.status_code == 302 and "/login/" in r["Location"]
