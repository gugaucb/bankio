import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class CardType(models.TextChoices):
    DEBIT = "DEBIT_CARD"
    CREDIT = "CREDIT_CARD"
    VIRTUAL = "VIRTUAL_CARD"
    TEMP_VIRTUAL = "TEMPORARY_VIRTUAL_CARD"
    SINGLE_USE = "SINGLE_USE_CARD"


class CardStatus(models.TextChoices):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    BLOCKED = "BLOCKED"
    EXPIRED = "EXPIRED"
    REPLACED = "REPLACED"


class Card(models.Model):
    account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="cards")
    type = models.CharField(max_length=32, choices=CardType.choices, default=CardType.DEBIT)
    status = models.CharField(max_length=10, choices=CardStatus.choices, default=CardStatus.ACTIVE)
    # Only last4 stored; full PAN never persisted (simulated issuer would tokenize).
    last4 = models.CharField(max_length=4)
    holder_name = models.CharField(max_length=120)
    expiry_month = models.PositiveSmallIntegerField(default=12)
    expiry_year = models.PositiveSmallIntegerField(default=2030)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    tx_limit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("2000.00"))
    daily_limit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("3000.00"))
    online_enabled = models.BooleanField(default=True)
    international_enabled = models.BooleanField(default=False)
    contactless_enabled = models.BooleanField(default=True)
    atm_enabled = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if not self.last4:
            self.last4 = f"{secrets.randbelow(10000):04d}"
        super().save(*args, **kwargs)

    @property
    def masked_number(self):
        return f"•••• •••• •••• {self.last4}"

    @property
    def is_expired(self):
        from datetime import date

        return date(self.expiry_year, self.expiry_month, 1) < date.today()

    def clean_limits(self):
        if self.tx_limit < 0 or self.daily_limit < 0 or self.credit_limit < 0:
            raise ValidationError("Card limits cannot be negative")

    def __str__(self):
        return f"{self.type} {self.masked_number} [{self.status}]"


class CardRequest(models.Model):
    """Customer card application. Credit cards require manager approval + limit."""

    class Status(models.TextChoices):
        PENDING = "PENDING"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        CANCELED = "CANCELED"

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                 related_name="card_requests")
    account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="card_requests")
    type = models.CharField(max_length=32, choices=CardType.choices, default=CardType.CREDIT)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    requested_limit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("2000.00"))
    approved_limit = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    decision_reason = models.CharField(max_length=255, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="card_requests_reviewed")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"CardRequest {self.pk} {self.customer} {self.type} [{self.status}]"


class CardTransaction(models.Model):
    card = models.ForeignKey(Card, on_delete=models.PROTECT, related_name="transactions")
    merchant = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=19, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    international = models.BooleanField(default=False)
    online = models.BooleanField(default=False)
    declined = models.BooleanField(default=False)
    decline_reason = models.CharField(max_length=64, blank=True)
    journal = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class CreditStatement(models.Model):
    card = models.ForeignKey(Card, on_delete=models.PROTECT, related_name="statements")
    period_start = models.DateField()
    period_end = models.DateField()
    amount_due = models.DecimalField(max_digits=19, decimal_places=2)
    paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("card", "period_end")
