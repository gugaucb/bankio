"""Security Central — MFA self-service + real OTP expiry (adversarial)."""
import logging
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.identity.services import MFAError, confirm_mfa_enable, disable_mfa, generate_otp, verify_otp


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(
        "mfa-alice", email="ma@t.io", password="Str0ng-pass!x", role="CUSTOMER")


def _client(user):
    c = Client(HTTP_USER_AGENT="Mfa/1")
    c.force_login(user)
    return c


@pytest.fixture(autouse=True)
def oob_capture():
    class _Sink(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.INFO)
            self.records = []

        def emit(self, record):
            self.records.append(record)

    sink = _Sink()
    lg = logging.getLogger("bankio.challenge")
    lg.addHandler(sink)
    lg.setLevel(logging.INFO)
    yield sink
    lg.removeHandler(sink)


def _delivered_enable_code(sink):
    for record in sink.records:
        msg = record.getMessage()
        if "mfa enable code" in msg:
            return msg.rsplit(": ", 1)[1].split()[0]
    raise AssertionError("no enable code delivered")


# ------------------------------------------------------- OTP temporal expiry

@pytest.mark.django_db
def test_otp_expires_after_ttl(alice):
    code = generate_otp(alice)
    assert verify_otp(alice, code) is True

    # fresh code aged beyond TTL fails closed even with the correct value
    code2 = generate_otp(alice)
    alice.otp_generated_at = timezone.now() - timedelta(minutes=6)
    alice.save(update_fields=["otp_generated_at"])
    assert verify_otp(alice, code2) is False
    assert alice.mfa_secret == ""          # consumed on expiry check


@pytest.mark.django_db
def test_legacy_secret_without_timestamp_fails_closed(alice):
    alice.mfa_secret = "abcdef123456"
    alice.otp_generated_at = None
    alice.save(update_fields=["mfa_secret", "otp_generated_at"])
    assert verify_otp(alice, "anything") is False


@pytest.mark.django_db
def test_otp_replay_rejected(alice):
    code = generate_otp(alice)
    assert verify_otp(alice, code) is True
    assert verify_otp(alice, code) is False   # single use


@pytest.mark.django_db
def test_wrong_codes_never_consume_correct_one(alice):
    code = generate_otp(alice)
    for _ in range(10):
        assert verify_otp(alice, "000000") is False
    assert verify_otp(alice, code) is True   # brute force does not burn the OTP


# ------------------------------------------------------------ self-service

@pytest.mark.django_db
def test_enable_flow_start_confirm_via_ui(alice, oob_capture):
    c = _client(alice)
    r = c.post("/app/security/", {"mfa_enable_start": "1"})
    assert r.status_code == 302
    code = _delivered_enable_code(oob_capture)
    r2 = c.post("/app/security/", {"mfa_enable_confirm": "1", "mfa_code": code})
    assert r2.status_code == 302
    alice.refresh_from_db()
    assert alice.mfa_enabled is True
    assert AuditLog.objects.filter(actor=alice, action="MFA_ENABLE_STARTED").exists()
    assert AuditLog.objects.filter(actor=alice, action="MFA_ENABLED").exists()


@pytest.mark.django_db
def test_enable_confirm_with_wrong_or_expired_code_rejected(alice):
    from apps.identity.services import start_mfa_enable

    start_mfa_enable(alice)
    with pytest.raises(MFAError):
        confirm_mfa_enable(alice, "000000")
    alice.refresh_from_db()
    assert alice.mfa_enabled is False       # never enabled without confirmation
    assert not AuditLog.objects.filter(action="MFA_ENABLED", actor=alice).exists()

    # expired correct code also rejected
    from apps.identity.services import generate_otp

    code = generate_otp(alice)
    alice.otp_generated_at = timezone.now() - timedelta(minutes=6)
    alice.save(update_fields=["otp_generated_at"])
    with pytest.raises(MFAError):
        confirm_mfa_enable(alice, code)


@pytest.mark.django_db
def test_disable_requires_password_reauthentication(alice):
    alice.mfa_enabled = True
    alice.save(update_fields=["mfa_enabled"])

    # plain session POST without password must NOT disable
    c = _client(alice)
    r = c.post("/app/security/", {"mfa_disable": "1"})
    assert r.status_code == 302
    alice.refresh_from_db()
    assert alice.mfa_enabled is True

    # wrong password rejected at service level too
    with pytest.raises(MFAError):
        disable_mfa(alice, "WrongPassword!")
    with pytest.raises(MFAError):
        disable_mfa(alice, "")

    # correct password disables and audits
    disable_mfa(alice, "Str0ng-pass!x")
    alice.refresh_from_db()
    assert alice.mfa_enabled is False
    assert AuditLog.objects.filter(actor=alice, action="MFA_DISABLED").exists()


@pytest.mark.django_db
def test_csrf_required_on_mfa_posts(alice):
    c = Client(enforce_csrf_checks=True)
    c.force_login(alice)
    for payload in ({"mfa_enable_start": "1"}, {"mfa_disable": "1"}):
        assert c.post("/app/security/", payload).status_code == 403
    alice.refresh_from_db()
    assert alice.mfa_enabled is False


@pytest.mark.django_db
def test_status_shown_and_no_code_in_html(alice, oob_capture):
    c = _client(alice)
    body = c.get("/app/security/").content.decode()
    assert "Two-Factor Authentication" in body and "Disabled" in body

    c.post("/app/security/", {"mfa_enable_start": "1"})
    body2 = c.get("/app/security/").content.decode()
    code = _delivered_enable_code(oob_capture)
    assert code not in body2                # OTP never rendered in HTML
    assert not AuditLog.objects.exclude(
        action__in=("MFA_ENABLE_STARTED",)).filter(metadata__icontains=code).exists()


@pytest.mark.django_db
def test_login_with_mfa_end_to_end(client, django_user_model, oob_capture):
    """Regression: enabled MFA still gates login via existing OTP flow."""
    u = django_user_model.objects.create_user(
        "mfa-e2e", email="me@t.io", password="Str0ng-pass!x", role="CUSTOMER")
    u.mfa_enabled = True
    u.save(update_fields=["mfa_enabled"])

    r = client.post("/login/", {"username": "mfa-e2e", "password": "Str0ng-pass!x"},
                    HTTP_USER_AGENT="E2E/1")
    assert r.status_code == 302 and "/otp/" in r["Location"]

    code = None
    for rec in oob_capture.records:      # attempt_login delivers via same channel
        msg = rec.getMessage()
        if "code for mfa-e2e" in msg:
            code = msg.rsplit(": ", 1)[1].split()[0]
    r2 = client.post("/otp/", {"code": code})
    assert r2.status_code == 302 and "/app" in r2["Location"]
