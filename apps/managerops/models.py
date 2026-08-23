"""Manager Operations domain: branches, manager authority, applications,
approvals (maker-checker), restrictions, notes, service requests."""
from decimal import Decimal

from django.conf import settings
from django.db import models


class ManagerLevel(models.TextChoices):
    RELATIONSHIP = "RELATIONSHIP_MANAGER", "Relationship Manager"
    BRANCH = "BRANCH_MANAGER", "Branch Manager"
    SENIOR = "SENIOR_MANAGER", "Senior Manager"
    REGIONAL = "REGIONAL_MANAGER", "Regional Manager"


LEVEL_ORDER = {
    ManagerLevel.RELATIONSHIP: 1,
    ManagerLevel.BRANCH: 2,
    ManagerLevel.SENIOR: 3,
    ManagerLevel.REGIONAL: 4,
}


class BankBranch(models.Model):
    branch_code = models.CharField(max_length=8, unique=True)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=200, blank=True)
    region = models.CharField(max_length=60, default="CENTER")
    status = models.CharField(max_length=10, default="ACTIVE")
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.PROTECT, related_name="managed_branches")

    def __str__(self):
        return f"{self.branch_code} {self.name}"


class ManagerProfile(models.Model):
    """Authority metadata for users with role MANAGER."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="manager_profile")
    level = models.CharField(max_length=24, choices=ManagerLevel.choices, default=ManagerLevel.RELATIONSHIP)
    branch = models.ForeignKey(BankBranch, null=True, on_delete=models.PROTECT, related_name="managers")

    @property
    def rank(self) -> int:
        return LEVEL_ORDER[self.level]

    def __str__(self):
        return f"{self.user} [{self.level}]"


class CustomerManagerAssignment(models.Model):
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="manager_assignments")
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="portfolio_assignments")
    branch = models.ForeignKey(BankBranch, null=True, on_delete=models.PROTECT)
    assigned_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, default="ACTIVE")

    class Meta:
        unique_together = ("customer", "manager")


class AccountNumberCounter(models.Model):
    """Per-branch sequence for server-side account number generation."""
    branch_code = models.CharField(max_length=8, unique=True)
    last = models.BigIntegerField(default=100000000)


class AccountApplication(models.Model):
    """Account opening workflow. Managers never type balances or account numbers."""

    STATES = ["APPLICATION", "PENDING_KYC", "PENDING_APPROVAL", "APPROVED", "ACTIVE", "REJECTED", "CANCELED"]

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="account_applications")
    product_type = models.CharField(max_length=12)  # CHECKING/SAVINGS/SALARY/JOINT/BUSINESS
    currency = models.CharField(max_length=3, default="USD")
    joint_with = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="joint_applications")
    business_name = models.CharField(max_length=140, blank=True)
    registration_number = models.CharField(max_length=32, blank=True)
    state = models.CharField(max_length=18, default="APPLICATION")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    branch = models.ForeignKey(BankBranch, null=True, on_delete=models.PROTECT)
    account = models.OneToOneField("accounts.Account", null=True, blank=True, on_delete=models.PROTECT)
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    ALLOWED = {
        "APPLICATION": {"PENDING_KYC", "PENDING_APPROVAL", "APPROVED", "REJECTED", "CANCELED"},
        "PENDING_KYC": {"PENDING_APPROVAL", "REJECTED", "CANCELED"},
        "PENDING_APPROVAL": {"APPROVED", "REJECTED", "CANCELED"},
        "APPROVED": {"ACTIVE"},
        "ACTIVE": set(),
        "REJECTED": set(),
        "CANCELED": set(),
    }

    def transition(self, new_state):
        if new_state not in self.ALLOWED.get(self.state, set()):
            raise ValueError(f"Illegal transition {self.state} -> {new_state}")
        self.state = new_state
        self.save(update_fields=["state"])


class ApprovalRequest(models.Model):
    """Generic maker-checker approval for restricted operations."""

    OPERATIONS = [
        ("ACCOUNT_OPENING", "Account opening"),
        ("LIMIT_INCREASE", "Transfer limit increase"),
        ("CREDIT_CARD_LIMIT", "Credit card limit"),
        ("LOAN_APPROVAL", "Loan approval"),
        ("ACCOUNT_UNBLOCK", "Account unblock"),
        ("ACCOUNT_CLOSURE", "Account closure"),
        ("FEE_WAIVER", "Fee waiver"),
        ("RATE_EXCEPTION", "Rate exception"),
        ("OVERDRAFT", "Overdraft facility"),
        ("HIGH_RISK_CUSTOMER", "High-risk customer acceptance"),
        ("ADJUSTMENT_REQUEST", "Financial adjustment"),
        ("ONBOARDING_REVIEW", "Public onboarding review"),
    ]
    STATUSES = [("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected"),
                ("CANCELED", "Canceled"), ("EXPIRED", "Expired")]

    operation_type = models.CharField(max_length=24, choices=OPERATIONS)
    resource_type = models.CharField(max_length=64, blank=True)
    resource_id = models.CharField(max_length=64, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="approvals_requested")
    required_level = models.CharField(max_length=24, choices=ManagerLevel.choices)
    amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    current_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="PENDING")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="approvals_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AccountRestriction(models.Model):
    TYPES = [
        ("DEBIT_BLOCK", "Debit block"), ("CREDIT_BLOCK", "Credit block"),
        ("TRANSFER_BLOCK", "Transfer block"), ("CARD_BLOCK", "Card block"),
        ("FULL_BLOCK", "Full block"), ("AML_HOLD", "AML hold"), ("LEGAL_HOLD", "Legal hold"),
    ]
    COMPLIANCE_ONLY = {"AML_HOLD", "LEGAL_HOLD"}

    account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="restrictions")
    restriction_type = models.CharField(max_length=16, choices=TYPES)
    reason = models.CharField(max_length=255)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    active = models.BooleanField(default=True)
    effective_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def lift(self, actor):
        if self.restriction_type in self.COMPLIANCE_ONLY:
            from .services import RestrictionError

            raise RestrictionError("COMPLIANCE_ONLY")


class ManagerNote(models.Model):
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="manager_notes")
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    category = models.CharField(max_length=32, default="GENERAL")
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class ServiceRequest(models.Model):
    TYPES = [("CARD_REPLACEMENT", "Card replacement"), ("ACCOUNT_STATEMENT", "Statement"),
             ("LIMIT_CHANGE", "Limit change"), ("ACCOUNT_CLOSURE", "Closure"),
             ("CONTACT_UPDATE", "Contact update"), ("ACCOUNT_REACTIVATION", "Reactivation"),
             ("PRODUCT_CHANGE", "Product change")]
    STATUSES = [("OPEN", "Open"), ("IN_PROGRESS", "In Progress"), ("WAITING_CUSTOMER", "Waiting Customer"),
                ("COMPLETED", "Completed"), ("CANCELED", "Canceled")]

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="service_requests")
    request_type = models.CharField(max_length=24, choices=TYPES)
    detail = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=18, choices=STATUSES, default="OPEN")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class Appointment(models.Model):
    TYPES = [("GENERAL_SERVICE", "General"), ("ACCOUNT_OPENING", "Account opening"),
             ("CREDIT_REVIEW", "Credit review"), ("INVESTMENT_REVIEW", "Investment review"),
             ("LOAN_REVIEW", "Loan review"), ("KYC_UPDATE", "KYC update")]

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_appointments")
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="appointments")
    appointment_type = models.CharField(max_length=20, choices=TYPES, default="GENERAL_SERVICE")
    scheduled_at = models.DateTimeField()
    notes = models.CharField(max_length=255, blank=True)
    completed = models.BooleanField(default=False)
    canceled = models.BooleanField(default=False)


class FeeWaiverRequest(models.Model):
    fee = models.CharField(max_length=64)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="fee_waivers")
    reason = models.CharField(max_length=255)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    valid_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
