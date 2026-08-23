from decimal import Decimal

from django.conf import settings
from django.db import models


class LoanProduct(models.Model):
    TYPES = [("PERSONAL", "Personal Loan"), ("AUTO", "Auto Loan"), ("MORTGAGE", "Mortgage")]

    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=80)
    type = models.CharField(max_length=12, choices=TYPES)
    min_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("1000"))
    max_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("50000"))
    base_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("9.99"))  # % a.a.


class LoanApplication(models.Model):
    STATUSES = [
        ("DRAFT", "Simulation"), ("SUBMITTED", "Application"), ("REVIEW", "Review"),
        ("APPROVED", "Approved"), ("REJECTED", "Rejected"), ("ACTIVE", "Active"),
        ("PAID", "Paid"), ("DEFAULTED", "Defaulted"),
    ]

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="loan_applications")
    product = models.ForeignKey(LoanProduct, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    term_months = models.PositiveSmallIntegerField(default=12)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUSES, default="DRAFT")
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    disbursed_account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class RepaymentSchedule(models.Model):
    application = models.ForeignKey(LoanApplication, on_delete=models.PROTECT, related_name="schedule")
    installment_no = models.PositiveSmallIntegerField()
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_at = models.DateTimeField(null=True, blank=True)
    journal = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        unique_together = ("application", "installment_no")
        ordering = ["installment_no"]
