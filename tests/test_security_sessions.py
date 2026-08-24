"""Security Central — Sessions: own-session visibility, revoke, IDOR."""
import pytest
from django.contrib.sessions.models import Session
from django.test import Client

from apps.audit.models import AuditLog
from apps.identity.services import SessionError, bind_session, revoke_other_sessions

UA_ALICE_WEB = "Alice-Browser/9"
UA_ALICE_PHONE = "Alice-Phone/2"
UA_BOB = "Bob-Browser/1"


def _client(user, ua):
    c = Client(HTTP_USER_AGENT=ua)
    c.force_login(user)
    c.session.save()
    return c


def _bind(c, user, ua):
    """Mirror what the login flow does after auth.login()."""
    req = type("R", (), {"META": {"HTTP_USER_AGENT": ua}, "session": c.session})()
    bind_session(req, user)


def _svc_req(c):
    return type("R", (), {"META": {}, "session": c.session})()


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(
        "ses-alice", email="sa@t.io", password="Str0ng-pass!x", role="CUSTOMER")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(
        "ses-bob", email="sb@t.io", password="Str0ng-pass!x", role="CUSTOMER")


# ------------------------------------------------------------------ listing

@pytest.mark.django_db
def test_lists_only_own_sessions_and_marks_current(alice, bob):
    web = _client(alice, UA_ALICE_WEB)
    phone = _client(alice, UA_ALICE_PHONE)
    _bind(web, alice, UA_ALICE_WEB)
    _bind(phone, alice, UA_ALICE_PHONE)

    bobc = _client(bob, UA_BOB)
    _bind(bobc, bob, UA_BOB)

    body = web.get("/app/security/").content.decode()
    assert "Active Sessions" in body and "Signed in" in body
    assert body.count("This session") == 1
    assert UA_ALICE_PHONE in body
    assert UA_BOB not in body


@pytest.mark.django_db
def test_stale_records_pruned_from_listing(alice):
    c = _client(alice, UA_ALICE_WEB)
    rec = alice.session_records.create(session_key="dead-key", user_agent="Zombie-UA-XYZ")
    Session.objects.filter(session_key="dead-key").delete()   # expired server-side
    body = c.get("/app/security/").content.decode()
    assert not alice.session_records.filter(pk=rec.pk).exists()
    assert "Zombie-UA-XYZ" not in body


# ------------------------------------------------------------------ revoking

@pytest.mark.django_db
def test_revoke_other_single_session(alice):
    current = _client(alice, UA_ALICE_WEB)
    victim = _client(alice, UA_ALICE_PHONE)
    vkey = victim.session.session_key
    _bind(victim, alice, UA_ALICE_PHONE)
    _bind(current, alice, UA_ALICE_WEB)

    n = revoke_other_sessions(alice, _svc_req(current), session_key=vkey)
    assert n == 1
    assert not Session.objects.filter(session_key=vkey).exists()
    # victim's cookie no longer authenticates
    r2 = victim.get("/app/security/")
    assert r2.status_code == 302 and "/login/" in r2["Location"]
    # current session untouched
    assert current.get("/app/security/").status_code == 200
    assert AuditLog.objects.filter(actor=alice, action="SESSION_REVOKED",
                                   metadata__count=1).exists()


@pytest.mark.django_db
def test_revoke_all_others_keeps_current(alice):
    current = _client(alice, UA_ALICE_WEB)
    victims = []
    for i in range(3):
        v = _client(alice, f"Dev/{i}")
        _bind(v, alice, f"Dev/{i}")
        victims.append(v)
    _bind(current, alice, UA_ALICE_WEB)
    before = Session.objects.count()

    n = revoke_other_sessions(alice, _svc_req(current))
    assert n == 3
    assert Session.objects.count() == before - 3
    for v in victims:
        assert not Session.objects.filter(
            session_key=v.session.session_key).exists()
    assert current.get("/app/security/").status_code == 200
    log = AuditLog.objects.get(action="OTHER_SESSIONS_REVOKED", actor=alice)
    assert log.metadata["count"] == 3


@pytest.mark.django_db
def test_cannot_revoke_current_or_foreign_session(alice, bob):
    current = _client(alice, UA_ALICE_WEB)
    _bind(current, alice, UA_ALICE_WEB)

    svc = _svc_req(current)
    with pytest.raises(SessionError):   # current session is protected
        revoke_other_sessions(alice, svc, session_key=current.session.session_key)

    foreign = _client(bob, UA_BOB)
    fkey = foreign.session.session_key
    with pytest.raises(SessionError):   # foreign key unknown to alice → no-op
        revoke_other_sessions(alice, svc, session_key=fkey)
    assert Session.objects.filter(session_key=fkey).exists()   # untouched
    assert foreign.get("/app/security/").status_code == 200    # still logged in
    assert not AuditLog.objects.filter(action="SESSION_REVOKED").exists()


@pytest.mark.django_db
def test_ui_revoke_buttons_and_csrf(alice):
    current = _client(alice, UA_ALICE_WEB)
    victim = _client(alice, UA_ALICE_PHONE)
    vkey = victim.session.session_key
    _bind(victim, alice, UA_ALICE_PHONE)
    _bind(current, alice, UA_ALICE_WEB)

    r = current.post("/app/security/", {"session": vkey, "revoke_session": "1"})
    assert r.status_code == 302
    assert not Session.objects.filter(session_key=vkey).exists()

    csrfless = Client(enforce_csrf_checks=True)
    csrfless.force_login(alice)
    r2 = csrfless.post("/app/security/", {"revoke_other_sessions": "1"})
    assert r2.status_code == 403


@pytest.mark.django_db
def test_manipulated_session_keys_are_safe_noops(alice):
    current = _client(alice, UA_ALICE_WEB)
    _bind(current, alice, UA_ALICE_WEB)
    before = Session.objects.count()
    with pytest.raises(SessionError):
        revoke_other_sessions(alice, _svc_req(current),
                              session_key="junk-key-that-does-not-exist")
    assert Session.objects.count() == before


@pytest.mark.django_db
def test_full_http_login_creates_session_record(client, django_user_model):
    """The real login flow binds a SessionRecord (no fixture simulation)."""
    django_user_model.objects.create_user(
        "ses-http", email="sh@t.io", password="Str0ng-pass!x", role="CUSTOMER")
    r = client.post("/login/", {"username": "ses-http", "password": "Str0ng-pass!x"},
                    HTTP_USER_AGENT=UA_ALICE_WEB)
    assert r.status_code == 302
    user = django_user_model.objects.get(username="ses-http")
    rec = user.session_records.get()
    assert rec.user_agent == UA_ALICE_WEB and rec.device_hash
