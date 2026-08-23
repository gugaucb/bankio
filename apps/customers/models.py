from django.conf import settings
from django.db import models


class Customer(models.Model):
    """Extended customer profile attached to a User."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_profile")
    customer_number = models.CharField(max_length=16, unique=True)
    segment = models.CharField(max_length=16, default="RETAIL", choices=[("RETAIL", "Retail"), ("PREMIUM", "Premium"), ("BUSINESS", "Business")])
    assigned_manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="managed_customers")
    branch = models.ForeignKey("managerops.BankBranch", null=True, blank=True, on_delete=models.PROTECT, related_name="customers")
    status = models.CharField(max_length=16, default="ACTIVE", choices=[
        ("ACTIVE", "Active"), ("RESTRICTED", "Restricted"), ("DORMANT", "Dormant"),
        ("UNDER_REVIEW", "Under review"), ("CLOSED", "Closed"),
    ])
    monthly_income = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    occupation = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=200, blank=True)
    preferred_currency = models.CharField(max_length=3, default="USD")
    marketing_opt_in = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer_number} {self.user}"
