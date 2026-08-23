import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models


def _generate_barcode():
    return uuid.uuid4().hex[:24].upper()


class Bill(models.Model):
    CATEGORIES = [("UTILITY", "Utility"), ("INVOICE", "Invoice"), ("SUBSCRIPTION", "Subscription")]

    biller = models.CharField(max_length=120)
    barcode = models.CharField(max_length=48, unique=True, default=_generate_barcode)
    category = models.CharField(max_length=16, choices=CATEGORIES, default="UTILITY")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    due_date = models.DateField(null=True, blank=True)


class Payment(models.Model):
    STATUSES = [("COMPLETED", "Completed"), ("FAILED", "Failed"), ("REVERSED", "Reversed")]

    account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="payments")
    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=19, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUSES, default="COMPLETED")
    idempotency_key = models.CharField(max_length=64, unique=True)
    journal = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recurring = models.CharField(max_length=16, blank=True, choices=[("", ""), ("WEEKLY", "Weekly"), ("MONTHLY", "Monthly")])
    next_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
