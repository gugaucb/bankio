"""Security Central — Devices: list real fields only, trust/revoke, IDOR."""
import pytest
from django.test import Client

from apps.audit.models import AuditLog
from apps.identity.models import Device

UA_ALICE = "AlicePhone/1.0"
UA_BOB = "BobLaptop/2.0"


@pytest.fixture(autouse=True)
def settings_media(db):
    yield


def _client(user):
    c = Client(enforce_csrf_checks=False, HTTP_USER_AGENT=UA_ALICE)
    c.force_login(user)
    return c


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(
        "dev-alice", email="da@t.io", password="Str0ng-pass!x", role="CUSTOMER")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(
        "dev-bob", email="db@t.io", password="Str0ng-pass!x", role="CUSTOMER")


def _device(user, name, trusted=False):
    return Device.objects.create(user=user, device_id=f"hash-{name}", name=name,
                                 trusted=trusted)


# ------------------------------------------------------------------- listing

@pytest.mark.django_db
def test_lists_only_real_fields_and_marks_current(alice):
    from django.test import RequestFactory

    from apps.identity.services import _device_hash, register_device

    rf = RequestFactory()
    req = rf.get("/", HTTP_USER_AGENT=UA_ALICE)   # same UA the test Client sends
    register_device(alice, req)   # current device: same UA hash as the client
    Device.objects.create(user=alice, device_id=_device_hash(req)[::-1],
                          name="Other browser")
    c = _client(alice)
    r = c.get("/app/security/")
    body = r.content.decode()
    assert "Devices" in body
    assert body.count("This device") == 1
    assert "First used" in body and "Last used" in body


@pytest.mark.django_db
def test_does_not_list_other_users_devices(alice, bob):
    mine = _device(alice, "Alice phone")
    theirs = _device(bob, "Bob laptop")
    body = _client(alice).get("/app/security/").content.decode()
    assert "Alice phone" in body
    assert "Bob laptop" not in body
    # the foreign device must never appear in a device action form; a bare
    # `str(pk) not in body` false-positives on incidental page content (CSS,
    # JS, chart colors) when the pk sequence happens to collide
    assert f'name="device" value="{theirs.pk}"' not in body
    assert f'>Bob laptop<' not in body                  # no foreign row rendered


# ------------------------------------------------------------------ actions

@pytest.mark.django_db
def test_trust_then_untrust_audited(alice):
    d = _device(alice, "phone")
    c = _client(alice)
    r = c.post("/app/security/", {"device": d.pk, "trust_device": "1"})
    assert r.status_code == 302
    d.refresh_from_db()
    assert d.trusted is True
    assert AuditLog.objects.filter(actor=alice, action="DEVICE_TRUSTED").exists()

    c.post("/app/security/", {"device": d.pk, "untrust_device": "1"})
    d.refresh_from_db()
    assert d.trusted is False
    assert AuditLog.objects.filter(actor=alice, action="DEVICE_UNTRUSTED").exists()


@pytest.mark.django_db
def test_revoke_removes_device_and_audits(alice):
    d = _device(alice, "phone", trusted=True)
    c = _client(alice)
    r = c.post("/app/security/", {"device": d.pk, "revoke_device": "1"})
    assert r.status_code == 302
    assert not Device.objects.filter(pk=d.pk).exists()
    assert AuditLog.objects.filter(actor=alice, action="DEVICE_REVOKED").exists()
    # audit must not leak the full device hash
    meta = AuditLog.objects.get(action="DEVICE_REVOKED").metadata
    assert all(len(v) <= 16 for v in meta.values())


@pytest.mark.django_db
def test_idor_cannot_touch_foreign_device(alice, bob):
    foreign = _device(bob, "Bob laptop", trusted=False)
    c = _client(alice)
    for action in ("trust_device", "untrust_device", "revoke_device"):
        c.post("/app/security/", {"device": foreign.pk, action: "1"})
    foreign.refresh_from_db() if Device.objects.filter(pk=foreign.pk).exists() else None
    # nothing changed and nothing deleted
    assert Device.objects.filter(pk=foreign.pk).exists()
    assert foreign.trusted is False   # unchanged (fixture instance still valid)
    assert not AuditLog.objects.filter(action__in=("DEVICE_TRUSTED", "DEVICE_REVOKED"),
                                       actor=alice).exists()


@pytest.mark.django_db
def test_manipulated_device_ids_are_noops(alice):
    _device(alice, "mine")
    c = _client(alice)
    for payload in ({"device": "", "trust_device": "1"},
                    {"device": "abc", "trust_device": "1"},
                    {"device": "999999", "revoke_device": "1"}):
        r = c.post("/app/security/", payload)
        assert r.status_code == 302      # redirect, silently no-op
    assert Device.objects.filter(user=alice).count() == 1


@pytest.mark.django_db
def test_csrf_required_for_device_actions(alice):
    d = _device(alice, "phone")
    c = Client(enforce_csrf_checks=True)
    c.force_login(alice)
    r = c.post("/app/security/", {"device": d.pk, "trust_device": "1"})
    assert r.status_code == 403
    d.refresh_from_db()
    assert d.trusted is False


@pytest.mark.django_db
def test_anonymous_redirected(aubrey=None):
    from django.contrib.auth import get_user_model
    u = get_user_model().objects.create_user("anon-dev", email="ad@t.io", password="x")
    _device(u, "phone")
    r = Client().get("/app/security/")
    assert r.status_code == 302 and "/login/" in r["Location"]


@pytest.mark.django_db
def test_trusted_state_feeds_is_new_device_semantics(alice):
    """The corrected semantics: trust flag now reachable; fraud signal helper
    reflects it without any rule change."""
    from django.test import RequestFactory

    from apps.identity.services import is_new_device, register_device, trust_device

    rf = RequestFactory()
    req = rf.get("/", HTTP_USER_AGENT=UA_ALICE, HTTP_ACCEPT_LANGUAGE="en")
    register_device(alice, req)
    assert is_new_device(alice, req) is True        # born untrusted
    d = Device.objects.get(user=alice)
    trust_device(alice, d.pk)
    assert is_new_device(alice, req) is False       # owner opted in
