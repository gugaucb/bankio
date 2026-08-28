"""TOTP MFA enrollment + login: QR/otpauth, verification gate, anti-replay,
brute-force guard, disable requires password+TOTP, secrets never leak."""
import pytest
from django.test import Client
from django.urls import reverse

import pyotp

from apps.audit.models import AuditLog
from apps.identity.models import User
from apps.identity.services import (
    MFAError, attempt_login, confirm_totp_enrollment, disable_mfa,
    start_totp_enrollment, verify_totp,
)
from tests.conftest import make_user

PW = "Totp-Pass-1"


@pytest.fixture
def totp_user(db):
    u = make_user("totp_user", password=PW, role="CUSTOMER")
    return u


def _totp(u):
    start_totp_enrollment(u)
    u.refresh_from_db()
    secret = u.totp_secret_enc  # need plaintext: decrypt via service helper
    from apps.identity.services import _fernet
    return pyotp.TOTP(_fernet().decrypt(u.totp_secret_enc.encode()).decode())


def test_enrollment_generates_otpauth_and_keeps_mfa_disabled(totp_user):
    data = start_totp_enrollment(totp_user)
    totp_user.refresh_from_db()
    assert totp_user.mfa_enabled is False                    # NOT enabled before verify
    assert data["uri"].startswith("otpauth://totp/Bankio:")
    assert data["secret"] in data["uri"]
    assert data["qr_svg"].lstrip().startswith("<?xml")       # local QR (SVG)
    assert "secret" not in str(AuditLog.objects.filter(actor=totp_user).values("metadata"))


@pytest.mark.django_db
def test_wrong_code_does_not_enable(totp_user):
    _totp(totp_user)
    with pytest.raises(MFAError):
        confirm_totp_enrollment(totp_user, "000000")
    totp_user.refresh_from_db()
    assert totp_user.mfa_enabled is False
    assert AuditLog.objects.filter(actor=totp_user, action="MFA_VERIFICATION_FAILED").exists()


@pytest.mark.django_db
def test_correct_code_enables(totp_user):
    t = _totp(totp_user)
    confirm_totp_enrollment(totp_user, t.now())
    totp_user.refresh_from_db()
    assert totp_user.mfa_enabled is True
    assert AuditLog.objects.filter(actor=totp_user, action="MFA_ENABLED").exists()


@pytest.mark.django_db
def test_login_requires_totp_when_enabled(totp_user):
    t = _totp(totp_user)
    confirm_totp_enrollment(totp_user, t.now())
    totp_user.refresh_from_db()
    from django.test import RequestFactory

    rf = RequestFactory()
    user, needs_otp = attempt_login("totp_user", PW, rf.post("/login/", HTTP_USER_AGENT="T/1"))
    assert user is not None and needs_otp is True            # password alone never completes


@pytest.mark.django_db
def test_totp_verify_and_replay_rejected(totp_user):
    t = _totp(totp_user)
    confirm_totp_enrollment(totp_user, t.now())
    code = t.now()
    assert verify_totp(totp_user, code) is True
    assert verify_totp(totp_user, code) is False             # same timestep -> replay refused


@pytest.mark.django_db
def test_otp_view_brute_force_guard(totp_user):
    t = _totp(totp_user)
    confirm_totp_enrollment(totp_user, t.now())
    c = Client(enforce_csrf_checks=False)
    rf = __import__("django.test", fromlist=["RequestFactory"]).RequestFactory()
    user, needs_otp = attempt_login("totp_user", PW, rf.post("/login/", HTTP_USER_AGENT="T/2"))
    assert needs_otp
    s = c.session
    s["pending_otp_user"] = totp_user.pk
    s.save()
    for _ in range(5):
        r = c.post("/otp/", {"code": "000000"})
    assert b"Too many attempts" in r.content or b"sign in again" in r.content
    assert not c.session.get("pending_otp_user")             # forced back to password step


@pytest.mark.django_db
def test_disable_requires_password_and_totp(totp_user):
    t = _totp(totp_user)
    confirm_totp_enrollment(totp_user, t.now())
    with pytest.raises(MFAError):
        disable_mfa(totp_user, PW, totp_code="000000")
    disable_mfa(totp_user, PW, totp_code=t.now())
    totp_user.refresh_from_db()
    assert totp_user.mfa_enabled is False


@pytest.mark.django_db
def test_secret_never_logged_or_in_audit(totp_user):
    t = _totp(totp_user)
    confirm_totp_enrollment(totp_user, t.now())
    secret = _fernet_secret(totp_user)
    for m in AuditLog.objects.filter(actor=totp_user).values_list("metadata", flat=True):
        assert secret not in str(m)


def _fernet_secret(u):
    from apps.identity.services import _fernet

    return _fernet().decrypt(u.totp_secret_enc.encode()).decode()


@pytest.mark.django_db
def test_full_login_via_otp_view_with_totp(totp_user):
    t = _totp(totp_user)
    confirm_totp_enrollment(totp_user, t.now())
    c = Client(enforce_csrf_checks=False)
    r = c.post("/login/", {"username": "totp_user", "password": PW})
    assert r.status_code == 302 and "/otp/" in r.url          # MFA required in browser flow
    r = c.post("/otp/", {"code": t.now()})
    assert r.status_code == 302 and "/otp/" not in r.url      # valid TOTP completes sign-in
    assert c.session.get("_auth_user_id")
