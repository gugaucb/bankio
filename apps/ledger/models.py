from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class LedgerAccount(models.Model):
    """Chart-of-accounts node. Bank liability accounts back customer balances."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    type = models.CharField(
        max_length=16,
        choices=[(t, t) for t in ("ASSET", "LIABILITY", "INCOME", "EXPENSE", "EQUITY")],
    )
    currency = models.CharField(max_length=3, default="USD")
    is_customer_account = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=["ACTIVE", "BLOCKED", "CLOSED"]),
                name="ledgeraccount_status_valid",
            ),
        ]

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        BLOCKED = "BLOCKED"
        CLOSED = "CLOSED"

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )

    def __str__(self):
        return f"{self.code} {self.name}"


class JournalEntry(models.Model):
    """
    A balanced set of ledger entries (one financial fact). Immutable once POSTED.
    status: DRAFT -> POSTED; corrections only via reversing journal.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        POSTED = "POSTED"

    reference = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default="DRAFT")
    currency = models.CharField(max_length=3, default="USD")
    posted_at = models.DateTimeField(null=True, blank=True)
    reverses = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")

    def balance_check(self):
        debits = Decimal("0")
        credits = Decimal("0")
        for e in self.entries.all():
            if e.side == "DEBIT":
                debits += e.amount
            else:
                credits += e.amount
        return debits, credits

    IMMUTABLE_FIELDS = {"reference", "description", "status", "posted_at", "reverses"}

    def save(self, *args, **kwargs):
        if self.pk:
            orig = type(self).objects.get(pk=self.pk)
            if orig.status == "POSTED":
                changed = {
                    f for f in self.IMMUTABLE_FIELDS
                    if getattr(orig, f) != getattr(self, f)
                }
                # linking a reversal pointer is the only permitted update
                if changed != {"reverses"} or self.status != "POSTED":
                    raise ValidationError("Posted journal entries are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Journal entries cannot be deleted.")

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=["DRAFT", "POSTED"]), name="journalentry_status_valid"
            ),
        ]


class LedgerEntry(models.Model):
    journal = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="entries")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="entries")
    side = models.CharField(max_length=6, choices=[("DEBIT", "Debit"), ("CREDIT", "Credit")])
    amount = models.DecimalField(max_digits=19, decimal_places=2)

    def save(self, *args, **kwargs):
        if self.amount <= 0:
            raise ValidationError("Ledger entry amounts must be positive.")
        if self.journal_id and JournalEntry.objects.get(pk=self.journal_id).status == "POSTED":
            raise ValidationError("Cannot modify entries of a posted journal.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ledger entries cannot be deleted.")

    class Meta:
        indexes = [models.Index(fields=["account", "side"])]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0), name="ledgerentry_amount_positive"
            ),
            models.CheckConstraint(
                check=models.Q(side__in=["DEBIT", "CREDIT"]), name="ledgerentry_side_valid"
            ),
        ]
