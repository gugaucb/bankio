from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    CUSTOMER = "CUSTOMER", "Customer"
    PREMIUM_CUSTOMER = "PREMIUM_CUSTOMER", "Premium Customer"
    MANAGER = "MANAGER", "Manager"
    CARD_OPS_ANALYST = "CARD_OPS_ANALYST", "Card Operations Analyst"
    COMPLIANCE_ANALYST = "COMPLIANCE_ANALYST", "Compliance Analyst"
    FRAUD_ANALYST = "FRAUD_ANALYST", "Fraud Analyst"
    SENIOR_FRAUD_ANALYST = "SENIOR_FRAUD_ANALYST", "Senior Fraud Analyst"
    FRAUD_MANAGER = "FRAUD_MANAGER", "Fraud Manager"
    SUPPORT_AGENT = "SUPPORT_AGENT", "Support Agent"
    ADMIN = "ADMIN", "Administrator"
    AUDITOR = "AUDITOR", "Auditor"


class User(AbstractUser):
    """Single user table; staff roles use role field, customers link to Customer profile."""

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=30, choices=Role.choices, default=Role.CUSTOMER)
    phone = models.CharField(max_length=20, blank=True)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.CharField(max_length=12, blank=True)  # demo OTP secret
    otp_generated_at = models.DateTimeField(null=True, blank=True)  # TTL anchor
    totp_secret_enc = models.TextField(blank=True)  # Fernet-encrypted TOTP secret (RFC 6238)
    totp_last_step = models.BigIntegerField(default=0)  # anti-replay: last accepted timestep
    failed_login_count = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    REQUIRED_FIELDS = ["email"]

    @property
    def is_customer(self):
        return self.role in (Role.CUSTOMER, Role.PREMIUM_CUSTOMER)

    @property
    def is_bank_staff(self):
        return self.role not in (Role.CUSTOMER, Role.PREMIUM_CUSTOMER)

    def has_role(self, *roles):
        return self.role in roles

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"


class Device(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    device_id = models.CharField(max_length=64)  # hash of UA + accept-language
    name = models.CharField(max_length=120, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    trusted = models.BooleanField(default=False)

    class Meta:
        unique_together = ("user", "device_id")


class SessionRecord(models.Model):
    """Minimal metadata for one login session (Django stores only the auth
    payload in django_session — nothing a safe security UI could show).
    Keyed by the real Django session_key; deleting both rows revokes access."""

    session_key = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_records")
    created_at = models.DateTimeField(auto_now_add=True)
    user_agent = models.CharField(max_length=200, blank=True)
    device_hash = models.CharField(max_length=64, blank=True)

    def __str__(self):
        return f"session {self.session_key[:8]}… of {self.user}"


class TourProgress(models.Model):
    """Server-side authority for the first-access tutorial: a row means the
    tour was finished (completed_at) or dismissed (skipped_at) for a given
    tour version. No row = the tour should auto-start on next dashboard load."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="tour_progress")
    tour_version = models.CharField(max_length=20)
    completed_at = models.DateTimeField(null=True, blank=True)
    skipped_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        state = "done" if self.completed_at else ("skipped" if self.skipped_at else "open")
        return f"tour {self.tour_version} {state} for {self.user}"
