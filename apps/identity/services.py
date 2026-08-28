"""Authentication domain services: lockout, OTP/MFA, device tracking."""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.utils import timezone

from .models import User

MAX_FAILED = 5
LOCKOUT_MINUTES = 15
OTP_TTL_MINUTES = 5


def _device_hash(request):
    ua = request.META.get("HTTP_USER_AGENT", "")
    lang = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    return hashlib.sha256(f"{ua}|{lang}".encode()).hexdigest()[:64]


def register_device(user, request):
    from .models import Device

    did = _device_hash(request)
    device, created = Device.objects.get_or_create(
        user=user, device_id=did, defaults={"name": request.META.get("HTTP_USER_AGENT", "")[:120]}
    )
    if created:
        # FASE 6: only a genuinely first-seen device row is "new" — trusted
        # devices logging in again must never trigger this.
        from apps.notifications.services import notify

        notify(recipient=user, category="SECURITY", kind="NEW_DEVICE",
               title="New device sign-in",
               body="Your account was accessed from a new device.",
               metadata={"device": (device.name or "")[:80]},
               dedup_key=f"NEW_DEVICE:{user.pk}:{did}")
    return device


def is_new_device(user, request):
    from .models import Device

    return not Device.objects.filter(user=user, device_id=_device_hash(request), trusted=True).exists()


def current_device_hash(request):
    """Hash of the calling device, comparable with Device.device_id."""
    return _device_hash(request)


class DeviceError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class SessionError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _own_device(user, device_pk):
    from .models import Device

    device = Device.objects.filter(pk=device_pk, user=user).first()
    if device is None:
        raise DeviceError("DEVICE_NOT_FOUND")   # owner-or-not-found: no IDOR leak
    return device


def trust_device(user, device_pk, request=None):
    """Owner marks a device as trusted. Explicit opt-in only — nothing else in
    the system flips this flag (fraude signal semantics unchanged)."""
    from apps.audit.services import record as audit

    device = _own_device(user, device_pk)
    if not device.trusted:
        device.trusted = True
        device.save(update_fields=["trusted"])
        audit(actor=user, action="DEVICE_TRUSTED", request=request, resource=device,
              metadata={"device_hash": device.device_id[:12]})
    return device


def untrust_device(user, device_pk, request=None):
    """Revoke trust only; the device record stays (history preserved)."""
    from apps.audit.services import record as audit

    device = _own_device(user, device_pk)
    if device.trusted:
        device.trusted = False
        device.save(update_fields=["trusted"])
        audit(actor=user, action="DEVICE_UNTRUSTED", request=request, resource=device,
              metadata={"device_hash": device.device_id[:12]})
    return device


def revoke_device(user, device_pk, request=None):
    """Remove the device record entirely. A future login re-registers it as an
    untrusted new device."""
    from apps.audit.services import record as audit

    device = _own_device(user, device_pk)
    audit(actor=user, action="DEVICE_REVOKED", request=request, resource=None,
          metadata={"device": device.name[:80], "device_hash": device.device_id[:12]})
    device.delete()
    return device


# ------------------------------------------------------------------ sessions

def bind_session(request, user):
    """Record minimal metadata for the freshly authenticated session.
    Called right after auth.login() so the security UI can list real sessions."""
    from .models import SessionRecord

    SessionRecord.objects.update_or_create(
        session_key=request.session.session_key,
        defaults={
            "user": user,
            "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:200],
            "device_hash": _device_hash(request),
        },
    )


def _live_session_keys(user):
    """session_keys of this user that still exist in the session store."""
    from django.contrib.sessions.models import Session

    keys = list(user.session_records.values_list("session_key", flat=True))
    return {s.session_key for s in Session.objects.filter(session_key__in=keys)}


def revoke_other_sessions(user, request, session_key=None):
    """Revoke one other session, or all others when session_key is None.
    Never touches the current session; foreign keys are ignored (no IDOR)."""
    from apps.audit.services import record as audit
    from django.contrib.sessions.models import Session

    current_key = request.session.session_key
    mine = {r.session_key: r for r in user.session_records.all()}
    if session_key is not None:
        if session_key == current_key:
            raise SessionError("CURRENT_SESSION")
        targets = [session_key] if session_key in mine else []
    else:
        targets = [k for k in mine if k != current_key]

    live = _live_session_keys(user)
    revoked_keys = []
    for key in targets:
        deleted, _ = Session.objects.filter(session_key=key).delete()
        if deleted or key not in live:
            mine[key].delete()
            revoked_keys.append(key)

    if not revoked_keys:
        raise SessionError("SESSION_NOT_FOUND")
    if session_key is not None:
        audit(actor=user, action="SESSION_REVOKED", request=request,
              metadata={"count": len(revoked_keys)})
    else:
        audit(actor=user, action="OTHER_SESSIONS_REVOKED", request=request,
              metadata={"count": len(revoked_keys)})
    return len(revoked_keys)


def generate_otp(user):
    """Generate a 6-digit OTP valid for OTP_TTL_MINUTES (demo: stored hashed on user)."""
    code = f"{secrets.randbelow(1000000):06d}"
    user.mfa_secret = hashlib.sha256(code.encode()).hexdigest()[:12]
    user.otp_generated_at = timezone.now()
    user.save(update_fields=["mfa_secret", "otp_generated_at"])
    return code  # in production this is sent via SMS/email, never displayed


def _otp_expired(user):
    if not user.otp_generated_at:
        return True   # secret without timestamp is unusable (legacy rows)
    return timezone.now() > user.otp_generated_at + timedelta(minutes=OTP_TTL_MINUTES)


def verify_otp(user, code):
    """Single-use, time-limited verification. Expired codes fail closed."""
    if not user.mfa_secret or not code:
        return False
    if _otp_expired(user):
        user.mfa_secret = ""
        user.save(update_fields=["mfa_secret"])
        return False
    ok = hashlib.sha256(code.encode()).hexdigest()[:12] == user.mfa_secret
    if ok:
        user.mfa_secret = ""
        user.save(update_fields=["mfa_secret"])
    return ok


# ----------------------------------------------------------- MFA self-service

def _fernet():
    """Symmetric envelope for the TOTP secret, keyed from the Django secret key."""
    import base64
    from cryptography.fernet import Fernet
    from django.conf import settings

    key = base64.urlsafe_b64encode(
        hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def totp_uri(user, secret):
    import urllib.parse

    return urllib.parse.quote(
        f"otpauth://totp/Bankio:{user.username}?secret={secret}&issuer=Bankio&algorithm=SHA1&digits=6&period=30",
        safe=":/?&=")


def totp_qr_svg(uri):
    """Render the otpauth:// URI as a local QR Code SVG (never an external service)."""
    import io
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def start_totp_enrollment(user, request=None):
    """Generate a TOTP secret for enrollment. mfa_enabled stays False until the
    user proves control with a valid code. Secret stored encrypted only."""
    from apps.audit.services import record as audit
    import pyotp

    secret = pyotp.random_base32()
    user.totp_secret_enc = _fernet().encrypt(secret.encode()).decode()
    user.totp_last_step = 0
    user.save(update_fields=["totp_secret_enc", "totp_last_step"])
    audit(actor=user, action="MFA_ENABLE_STARTED", request=request,
          metadata={"method": "TOTP"})  # never the secret itself
    uri = totp_uri(user, secret)
    return {"secret": secret, "uri": uri, "qr_svg": totp_qr_svg(uri)}


def get_pending_totp_data(user):
    """Re-render the enrollment panel from the already-pending secret (no rotation)."""
    if not user.totp_secret_enc:
        return None
    try:
        secret = _fernet().decrypt(user.totp_secret_enc.encode()).decode()
    except Exception:
        return None
    uri = totp_uri(user, secret)
    return {"secret": secret, "uri": uri, "qr_svg": totp_qr_svg(uri)}


def verify_totp(user, code, record_step=True):
    """RFC 6238 verification with ±1 step drift window and strict anti-replay:
    a timestep may be accepted once (user.totp_last_step)."""
    import pyotp

    if not user.totp_secret_enc or not code or not code.strip().isdigit():
        return False
    try:
        secret = _fernet().decrypt(user.totp_secret_enc.encode()).decode()
    except Exception:
        return False
    totp = pyotp.TOTP(secret)
    matched = totp.verify(str(code).strip(), valid_window=1)
    if not matched:
        return False
    if record_step:
        import time as _time

        step = int(_time.time()) // 30
        if step <= user.totp_last_step:
            return False  # replay of an already-consumed timestep
        user.totp_last_step = step
        user.save(update_fields=["totp_last_step"])
    return True


def confirm_totp_enrollment(user, code, request=None):
    """Activate TOTP MFA only after a valid code proves the authenticator works."""
    from apps.audit.services import record as audit

    if not user.totp_secret_enc:
        raise MFAError("NO_PENDING_ENROLLMENT")
    if not verify_totp(user, code, record_step=False):
        audit(actor=user, action="MFA_VERIFICATION_FAILED", request=request,
              metadata={"method": "TOTP"})
        raise MFAError("INVALID_OR_EXPIRED_CODE")
    user.mfa_enabled = True
    user.save(update_fields=["mfa_enabled"])
    audit(actor=user, action="MFA_ENABLED", request=request, metadata={"method": "TOTP"})
    from apps.notifications.services import notify

    notify(recipient=user, category="SECURITY", kind="MFA_ENABLED",
           title="MFA enabled",
           body="Two-factor authentication (authenticator app) was enabled on your account.",
           dedup_key=f"MFA_ENABLED:{user.pk}:{user.pk}")
    return True


class MFAError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def start_mfa_enable(user, request=None):
    """Issue + deliver an OTP that proves control of the second factor."""
    from apps.audit.services import record as audit
    import logging

    code = generate_otp(user)
    logging.getLogger("bankio.challenge").info(
        "[step-up] mfa enable code for %s: %s (valid %s minutes)",
        user.username, code, OTP_TTL_MINUTES)
    audit(actor=user, action="MFA_ENABLE_STARTED", request=request,
          metadata={"expires_in_minutes": OTP_TTL_MINUTES})
    return code


def confirm_mfa_enable(user, code, request=None):
    """Enable MFA only after a valid, unexpired code proves the second factor."""
    from apps.audit.services import record as audit

    if not verify_otp(user, code):
        raise MFAError("INVALID_OR_EXPIRED_CODE")
    user.mfa_enabled = True
    user.save(update_fields=["mfa_enabled"])
    audit(actor=user, action="MFA_ENABLED", request=request)
    from apps.notifications.services import notify

    notify(recipient=user, category="SECURITY", kind="MFA_ENABLED",
           title="MFA enabled",
           body="Two-factor authentication was enabled on your account.",
           dedup_key=f"MFA_ENABLED:{user.pk}:{user.pk}")
    return True


def disable_mfa(user, password, request=None, totp_code=""):
    """Disable MFA only after password reauthentication AND, when the user has a
    TOTP authenticator enrolled, a currently valid TOTP code."""
    from apps.audit.services import record as audit

    if not password or not user.check_password(password):
        raise MFAError("REAUTHENTICATION_REQUIRED")
    if user.totp_secret_enc and not verify_totp(user, totp_code or "", record_step=False):
        raise MFAError("INVALID_OR_EXPIRED_CODE")
    user.mfa_enabled = False
    user.mfa_secret = ""
    user.save(update_fields=["mfa_enabled", "mfa_secret"])
    audit(actor=user, action="MFA_DISABLED", request=request)
    from apps.notifications.services import notify

    notify(recipient=user, category="SECURITY", kind="MFA_DISABLED",
           title="MFA disabled",
           body="Two-factor authentication was disabled on your account. "
                "If this was not you, contact support immediately.",
           dedup_key=f"MFA_DISABLED:{user.pk}:{user.pk}")
    return True


class LoginLocked(Exception):
    pass


class LoginRiskBlocked(Exception):
    """The fraud engine's effective decision refuses this login (fail-closed
    or BLOCK under enforcement). Evidence is already persisted by the engine."""


class ProfileActionBlocked(Exception):
    """A sensitive profile action (PASSWORD_CHANGE / PROFILE_UPDATE) was
    refused by the engine's effective decision or failed fail-closed."""


def sensitive_action_decision(evaluation):
    """Server-side-only mapping of a stored evaluation for a sensitive
    profile action. Engine failure outside observational modes → BLOCK
    (PASSWORD_CHANGE/PROFILE_UPDATE are unknown to the FAIL_OPEN matrix,
    so resolve_failure returns FAIL_CLOSED)."""
    from apps.fraud.failsafe import FAIL_OPEN, resolve_failure
    from apps.fraud.models import RiskEvaluation
    from apps.fraud.modes import effective_decision, get_mode

    if evaluation is None:
        if resolve_failure("PASSWORD_CHANGE") != FAIL_OPEN and get_mode() not in (
            RiskEvaluation.EngineMode.SHADOW, "DISABLED"):
            return "BLOCK"
        return "ALLOW"
    action = effective_decision(evaluation)
    if action == RiskEvaluation.Decision.BLOCK:
        return "BLOCK"
    if action == RiskEvaluation.Decision.CHALLENGE:
        return "CHALLENGE"
    return "ALLOW"


def _effective_login_action(evaluation):
    """Server-side-only mapping of a stored LOGIN evaluation to an action.

    The decision comes exclusively from the RiskEvaluation row + current
    engine mode — never from client input. SHADOW/DISABLED never interfere;
    an engine failure follows the failsafe matrix (LOGIN = FAIL_CLOSED, so
    outside observational modes the login must NOT proceed).
    """
    from apps.fraud.failsafe import FAIL_OPEN, resolve_failure
    from apps.fraud.models import RiskEvaluation
    from apps.fraud.modes import effective_decision, get_mode

    if evaluation is None:
        if resolve_failure("LOGIN") != FAIL_OPEN and get_mode() not in (
            RiskEvaluation.EngineMode.SHADOW, "DISABLED"):
            return "BLOCK"
        return "ALLOW"
    action = effective_decision(evaluation)
    if action == RiskEvaluation.Decision.BLOCK:
        return "BLOCK"
    if action == RiskEvaluation.Decision.CHALLENGE:
        return "CHALLENGE"
    return "ALLOW"


def _deliver_otp(user, purpose):
    code = generate_otp(user)
    import logging

    logging.getLogger("bankio.challenge").info(
        "[step-up] %s code for %s: %s (valid %s minutes)",
        purpose, user.username, code, OTP_TTL_MINUTES)


def _login_risk_evaluation(user, request, device=None):
    """Run the real LOGIN operation through the fraud engine.

    Returns the RiskEvaluation (or None if evaluation could not run). The
    engine itself persists FAILED snapshots before re-raising; in SHADOW mode
    an engine error must not break authentication — evidence stays recorded.
    Enforcement decisions are consumed by later rollout stages only.
    """
    from apps.fraud.auth_risk import evaluate_login
    from apps.fraud.failsafe import record_failure

    try:
        return evaluate_login(
            user,
            request=request,
            device_id=getattr(device, "device_id", "") or "",
            ip=(request.META.get("REMOTE_ADDR") if request else "") or "",
        )
    except Exception as exc:
        # FAILED snapshot persisted by the engine; strategy-standardized audit here
        record_failure("LOGIN", exc, actor=user)
        return None


def attempt_login(username, password, request):
    """Returns (user, needs_otp). Raises LoginLocked when temporarily locked.

    The identifier may be the username OR the account email (portal applicants
    sign in with the email from their application; usernames are generated).
    """
    identifier = str(username or "").strip()
    user = User.objects.filter(username=identifier).first()
    if user is None and identifier:
        user = User.objects.filter(email__iexact=identifier).first()
    if user is None:
        return None, False
    resolved_username = user.username

    now = timezone.now()
    if user.locked_until and user.locked_until > now:
        raise LoginLocked(f"Account locked until {user.locked_until.isoformat()}")

    user = authenticate(request, username=resolved_username, password=password)
    if user is None:
        u = User.objects.filter(username=resolved_username).first()
        if u:
            u.failed_login_count += 1
            if u.failed_login_count >= MAX_FAILED:
                u.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                u.failed_login_count = 0
            u.save(update_fields=["failed_login_count", "locked_until"])
            from apps.audit.services import record
            record(actor=u, action="LOGIN_FAILED", request=request)
        return None, False

    user.failed_login_count = 0
    user.locked_until = None
    user.save(update_fields=["failed_login_count", "locked_until"])
    device = register_device(user, request)

    # Risk evaluation runs AFTER credential validation and OUTSIDE any atomic
    # block, so the evidence (RiskEvaluation) survives aborted logins (INV 9).
    # Order: lockout → credentials → device → RISK → OTP → session → audit.
    # In SHADOW mode nothing here can block a valid login; enforcement comes
    # only in later rollout stages.
    evaluation = _login_risk_evaluation(user, request, device)

    # CHALLENGE_ONLY/enforcement: the stored evaluation decides. CHALLENGE
    # reuses the existing OTP step-up (same pending_otp_user flow as MFA) —
    # users without MFA enabled get the same one-time challenge infrastructure.
    action = _effective_login_action(evaluation)
    if action == "BLOCK":
        from apps.audit.services import record

        record(actor=user, action="LOGIN_RISK_DENIED", request=request,
               metadata={"evaluation": getattr(evaluation, "pk", None)})
        raise LoginRiskBlocked("sign-in refused by risk policy")
    if action == "CHALLENGE" or user.mfa_enabled:
        if user.mfa_enabled and user.totp_secret_enc:
            # TOTP authenticator: the code comes from the user's app — nothing to deliver
            return user, True
        _deliver_otp(user, "mfa login" if user.mfa_enabled else "risk challenge")
        return user, True

    from apps.audit.services import record
    record(actor=user, action="LOGIN", request=request)
    return user, False
