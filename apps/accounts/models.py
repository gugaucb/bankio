from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AccountStatus(models.TextChoices):
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class AccountType(models.TextChoices):
    CHECKING = "CHECKING"
    SAVINGS = "SAVINGS"
    SALARY = "SALARY"
    JOINT = "JOINT"
    BUSINESS = "BUSINESS"


class Account(models.Model):
    """Customer-facing account. Balance is a projection derived from the ledger."""

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="accounts"
    )
    account_number = models.CharField(max_length=16, unique=True)
    branch = models.CharField(max_length=8, default="0001")
    type = models.CharField(max_length=12, choices=AccountType.choices, default=AccountType.CHECKING)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=10, choices=AccountStatus.choices, default=AccountStatus.ACTIVE)
    ledger_account = models.OneToOneField("ledger.LedgerAccount", on_delete=models.PROTECT, related_name="bank_account")
    tx_limit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal(settings.BANKING_DEFAULT_TX_LIMIT))
    daily_limit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal(settings.BANKING_DEFAULT_DAILY_LIMIT))
    blocked_amount = models.DecimalField(max_digits=19, decimal_places=2, default=Decimal("0"))
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.blocked_amount < 0:
            raise ValidationError("blocked_amount cannot be negative")

    @property
    def current_balance(self) -> Decimal:
        from apps.ledger.services import account_balance

        return account_balance(self.ledger_account)

    @property
    def available_balance(self) -> Decimal:
        return self.current_balance - self.blocked_amount

    def __str__(self):
        return f"{self.account_number} ({self.type}, {self.currency})"


class Beneficiary(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="beneficiaries")
    name = models.CharField(max_length=120)
    bank_name = models.CharField(max_length=80, default="Bankio")
    account_number = models.CharField(max_length=24)
    currency = models.CharField(max_length=3, default="USD")
    is_external = models.BooleanField(default=False)
    verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("owner", "account_number")
