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
    # cryptographic proof fields (set once at posting; see canonical.py)
    payload_hash = models.CharField(max_length=64, null=True, blank=True, editable=False)
    previous_entry_hash = models.CharField(max_length=64, null=True, blank=True, editable=False)
    chain_hash = models.CharField(max_length=64, null=True, blank=True, editable=False)
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


class LedgerProofBatch(models.Model):
    """A sealed Merkle commitment over a contiguous range of posted journals.

    Once SEALED the membership range, root and signature are immutable.
    """

    class Status(models.TextChoices):
        OPEN = "OPEN"
        SEALED = "SEALED"
        ANCHORED = "ANCHORED"
        VERIFIED = "VERIFIED"
        FAILED = "FAILED"

    sequence = models.PositiveBigIntegerField(unique=True)
    first_journal_id = models.PositiveBigIntegerField()
    last_journal_id = models.PositiveBigIntegerField()
    entry_count = models.PositiveIntegerField()
    merkle_root = models.CharField(max_length=64)
    previous_batch_hash = models.CharField(max_length=64)
    batch_manifest_hash = models.CharField(max_length=64, blank=True)
    canonicalization_version = models.CharField(max_length=32)
    hash_algorithm = models.CharField(max_length=32, default="SHA-256")
    status = models.CharField(max_length=10, choices=Status.choices, default="SEALED")
    signature = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sealed_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.pk:
            orig = type(self).objects.get(pk=self.pk)
            if orig.status != self.Status.OPEN and (
                (orig.first_journal_id, orig.last_journal_id, orig.merkle_root,
                 orig.entry_count, orig.signature) !=
                (self.first_journal_id, self.last_journal_id, self.merkle_root,
                 self.entry_count, self.signature)
            ):
                raise ValueError("Sealed proof batches are immutable.")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(first_journal_id__lte=models.F("last_journal_id")),
                name="proofbatch_range_valid",
            ),
        ]


class LedgerAnchor(models.Model):
    """External anchoring record for a sealed proof batch."""

    class Status(models.TextChoices):
        CREATED = "CREATED"
        SUBMITTED = "SUBMITTED"
        CONFIRMING = "CONFIRMING"
        CONFIRMED = "CONFIRMED"
        FAILED = "FAILED"
        SUPERSEDED = "SUPERSEDED"

    batch = models.ForeignKey(LedgerProofBatch, on_delete=models.PROTECT, related_name="anchors")
    provider = models.CharField(max_length=64)
    anchor_reference = models.CharField(max_length=128, blank=True)
    idempotency_key = models.CharField(max_length=200)
    commitment = models.CharField(max_length=64)
    status = models.CharField(max_length=12, choices=Status.choices, default="CREATED")
    error = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)


class LedgerIdempotencyRecord(models.Model):
    """Marks a financial operation as already executed.

    A unique key maps to the journal it produced. Callers must check this
    BEFORE posting and write it in the SAME transaction as the posting,
    so retries - sequential or serialized by row locks - see exactly one
    financial movement per key.
    """

    key = models.CharField(max_length=160, unique=True)
    operation = models.CharField(max_length=64)
    journal = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="idempotency_records")
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


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
