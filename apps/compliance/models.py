from django.conf import settings
from django.db import models


class FraudRule(models.Model):
    RULE_TYPES = [
        ("AMOUNT_ABOVE", "Amount above threshold"),
        ("VELOCITY", "Velocity (transfers per 10min)"),
        ("NEW_DEVICE_HIGH_VALUE", "New device + high value"),
    ]
    ACTIONS = [("REVIEW", "Send to review"), ("BLOCK", "Block")]

    name = models.CharField(max_length=100)
    rule_type = models.CharField(max_length=32, choices=RULE_TYPES)
    action = models.CharField(max_length=10, choices=ACTIONS, default="REVIEW")
    threshold = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.rule_type}/{self.action})"


class FraudAlert(models.Model):
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="fraud_alerts")
    rule = models.ForeignKey(FraudRule, null=True, on_delete=models.SET_NULL)
    reason = models.TextField()
    severity = models.CharField(max_length=10, default="MEDIUM")
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class KYCReview(models.Model):
    STATUSES = [("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")]

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="kyc_reviews")
    status = models.CharField(max_length=10, choices=STATUSES, default="PENDING")
    risk_level = models.CharField(max_length=8, default="LOW", choices=[(l, l) for l in ("LOW", "MEDIUM", "HIGH")])
    notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
