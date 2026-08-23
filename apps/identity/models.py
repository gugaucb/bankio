from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    CUSTOMER = "CUSTOMER", "Customer"
    PREMIUM_CUSTOMER = "PREMIUM_CUSTOMER", "Premium Customer"
    MANAGER = "MANAGER", "Manager"
    CARD_OPS_ANALYST = "CARD_OPS_ANALYST", "Card Operations Analyst"
    COMPLIANCE_ANALYST = "COMPLIANCE_ANALYST", "Compliance Analyst"
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
