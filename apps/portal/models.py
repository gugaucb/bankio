"""Public portal domain: digital account applications opened by visitors.

The portal never creates bank accounts directly. Applications flow through
identity/KYC/manager review using the existing managerops + compliance domains.
"""
import uuid
from django.conf import settings
from django.db import models


class ApplicationStatus(models.TextChoices):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    IDENTITY_REVIEW = "IDENTITY_REVIEW"
    KYC_REVIEW = "KYC_REVIEW"
    ADDITIONAL_INFORMATION_REQUIRED = "ADDITIONAL_INFORMATION_REQUIRED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACCOUNT_CREATED = "ACCOUNT_CREATED"


PRODUCTS = [
    ("CHECKING", "Checking Account"),
    ("SAVINGS", "Savings Account"),
    ("DEBIT_CARD", "Debit Card"),
    ("CREDIT_CARD", "Credit Card"),
    ("INVESTMENT", "Investment Account"),
    ("PREMIUM", "Premium Banking"),
]


class AccountApplication(models.Model):
    """A public account application (pre-customer). One per applicant email."""

    reference = models.CharField(max_length=24, unique=True)
    # draft data captured step-by-step
    data = models.JSONField(default=dict, blank=True)
    current_step = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=36, choices=ApplicationStatus.choices,
                              default=ApplicationStatus.DRAFT)
    email = models.EmailField(blank=True)  # searchable/resume key once contact step done
    products = models.JSONField(default=list, blank=True)

    resume_token = models.UUIDField(default=uuid.uuid4, unique=True)
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True)

    risk_level = models.CharField(max_length=8, default="LOW",
                                  choices=[(l, l) for l in ("LOW", "MEDIUM", "HIGH")])
    decision_reason = models.CharField(max_length=255, blank=True)
    temp_password = models.CharField(max_length=64, blank=True)  # demo: shown once on status page
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="portal_applications_reviewed")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="portal_application")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["email"]), models.Index(fields=["status"])]

    def __str__(self):
        return f"{self.reference} [{self.status}]"

    @property
    def full_name(self):
        return self.data.get("full_name", "")

    def next_step_label(self):
        return {
            ApplicationStatus.SUBMITTED: "Identity verification",
            ApplicationStatus.IDENTITY_REVIEW: "Identity verification",
            ApplicationStatus.KYC_REVIEW: "KYC review",
            ApplicationStatus.UNDER_REVIEW: "Banker review",
            ApplicationStatus.ADDITIONAL_INFORMATION_REQUIRED: "Awaiting additional information",
            ApplicationStatus.APPROVED: "Account creation",
            ApplicationStatus.ACCOUNT_CREATED: "Welcome — sign in",
            ApplicationStatus.REJECTED: "Application closed",
        }.get(self.status, "Start your application")
