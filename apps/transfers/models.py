from decimal import Decimal

from django.conf import settings
from django.db import models


class TransferStatus(models.TextChoices):
    CREATED = "CREATED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    REVERSED = "REVERSED"
    UNDER_REVIEW = "UNDER_REVIEW"


# Explicit legal transitions of the transfer state machine.
ALLOWED_TRANSITIONS = {
    "CREATED": {"PENDING", "PROCESSING", "FAILED", "CANCELED"},
    "PENDING": {"PROCESSING", "FAILED", "CANCELED", "UNDER_REVIEW"},
    "PROCESSING": {"COMPLETED", "FAILED", "UNDER_REVIEW"},
    "UNDER_REVIEW": {"PROCESSING", "FAILED", "CANCELED"},
    "COMPLETED": {"REVERSED"},
    "FAILED": set(),
    "CANCELED": set(),
    "REVERSED": set(),
}


class Transfer(models.Model):
    reference = models.CharField(max_length=40, unique=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    source_account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="outgoing_transfers")
    # Internal destination (Bankio account) OR beneficiary for external
    destination_account = models.ForeignKey(
        "accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="incoming_transfers"
    )
    beneficiary = models.ForeignKey("accounts.Beneficiary", null=True, blank=True, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=19, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    description = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=TransferStatus.choices, default=TransferStatus.CREATED)
    journal = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT)
    failure_reason = models.CharField(max_length=255, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    recurrence = models.CharField(max_length=16, blank=True, choices=[("", ""), ("WEEKLY", "Weekly"), ("MONTHLY", "Monthly")])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_transfers")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="approved_transfers")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def transition(self, new_status):
        if new_status not in ALLOWED_TRANSITIONS.get(self.status, set()):
            raise ValueError(f"Illegal transition {self.status} -> {new_status}")
        self.status = new_status
        self.save(update_fields=["status", "updated_at"])

    @property
    def fee(self) -> Decimal:
        return Decimal("0")  # internal transfers free; external simulated flat fee

    def __str__(self):
        return f"{self.reference} {self.amount} {self.currency} [{self.status}]"
