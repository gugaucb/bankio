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


def generate_otp(user):
    """Generate a 6-digit OTP valid for 5 minutes (demo: stored hashed on user)."""
    code = f"{secrets.randbelow(1000000):06d}"
    user.mfa_secret = hashlib.sha256(code.encode()).hexdigest()[:12]
    user.save(update_fields=["mfa_secret"])
    return code  # in production this is sent via SMS/email, never displayed


def verify_otp(user, code):
    if not user.mfa_secret or not code:
        return False
    ok = hashlib.sha256(code.encode()).hexdigest()[:12] == user.mfa_secret
    if ok:
        user.mfa_secret = ""
        user.save(update_fields=["mfa_secret"])
    return ok


class LoginLocked(Exception):
    pass


def attempt_login(username, password, request):
    """Returns (user, needs_otp). Raises LoginLocked when temporarily locked."""
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return None, False

    now = timezone.now()
    if user.locked_until and user.locked_until > now:
        raise LoginLocked(f"Account locked until {user.locked_until.isoformat()}")

    user = authenticate(request, username=username, password=password)
    if user is None:
        u = User.objects.filter(username=username).first()
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
    register_device(user, request)

    if user.mfa_enabled:
        generate_otp(user)  # would be delivered out-of-band; demo seeds disable MFA or expose via mailhog-style log
        return user, True

    from apps.audit.services import record
    record(actor=user, action="LOGIN", request=request)
    return user, False
