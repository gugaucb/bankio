"""Portal application workflow: draft -> submit -> review -> decision.

Rules (enforced here, never in views/templates):
  - application submission is idempotent (unique idempotency key + status guard)
  - duplicate applicants (existing customer email) are rejected
  - the portal NEVER creates an active bank account directly;
    activation goes through managerops approval + KYC
"""
import secrets
from datetime import date, datetime

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record as audit
from apps.compliance.models import KYCReview
from apps.identity.models import User

from .models import PRODUCTS, AccountApplication, ApplicationStatus

# Wizard steps: key -> list of required fields
STEPS = [
    ("personal", ["full_name", "date_of_birth"]),
    ("contact", ["email", "phone"]),
    ("address", ["address"]),
    ("identity", ["national_id", "source_of_funds"]),
    ("employment", ["occupation", "employment_status"]),
    ("income", ["monthly_income"]),
    ("tax", ["tax_residency"]),
    ("products", []),
    ("consents", []),
]
TOTAL_STEPS = len(STEPS)

HIGH_RISK_PRODUCTS = {"CREDIT_CARD", "PREMIUM", "INVESTMENT"}


class ApplicationError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _new_reference():
    return f"BNK-APP-{secrets.randbelow(900000) + 100000}"


def start_application(data=None) -> AccountApplication:
    app = AccountApplication.objects.create(reference=_new_reference(), data=data or {})
    return app


def get_or_resume(*, reference: str = "", email: str = "", token: str = "") -> AccountApplication | None:
    """Resume a draft by reference+email, or directly via secret resume token."""
    try:
        if token:
            return AccountApplication.objects.get(resume_token=token)
        app = AccountApplication.objects.get(reference=reference)
    except AccountApplication.DoesNotExist:
        return None
    if token:
        return app
    if app.email and email.strip().lower() == app.email.lower():
        return app
    return None


def validate_step(step_index: int, data: dict):
    """Return error code string or None."""
    _, fields = STEPS[step_index]
    for f in fields:
        if not str(data.get(f, "")).strip():
            return f"MISSING_{f.upper()}"
    if "date_of_birth" in data and data.get("date_of_birth"):
        dob = datetime.strptime(str(data["date_of_birth"]), "%Y-%m-%d").date()
        age = (date.today() - dob).days // 365
        if age < 18:
            return "UNDERAGE"
    if step_index == 1:  # contact
        email = str(data.get("email", ""))
        if "@" not in email or "." not in email.split("@")[-1]:
            return "INVALID_EMAIL"
    if step_index == 5 and data.get("monthly_income") is not None:
        try:
            if float(data["monthly_income"]) < 0:
                return "INVALID_INCOME"
        except (TypeError, ValueError):
            return "INVALID_INCOME"
    return None


@transaction.atomic
def save_step(app: AccountApplication, step_index: int, form: dict, products=None) -> str:
    """Merge validated step data into the draft. Returns error code or ""."""
    if app.status != ApplicationStatus.DRAFT:
        raise ApplicationError("APPLICATION_ALREADY_SUBMITTED")
    err = validate_step(step_index, form)
    if err:
        return err
    clean = {k: v for k, v in form.items() if k in {f for _, fs in STEPS for f in fs}}
    app.data.update(clean)
    app.current_step = max(app.current_step, min(step_index + 2, TOTAL_STEPS))
    if step_index == 1:
        app.email = clean.get("email", app.email)
    if products is not None:
        valid = {code for code, _ in PRODUCTS}
        app.products = [p for p in products if p in valid]
    app.save()
    return ""


def _risk_level(app: AccountApplication) -> str:
    income = float(app.data.get("monthly_income") or 0)
    risky = HIGH_RISK_PRODUCTS.intersection(set(app.products))
    if income >= 25000 or len(risky) >= 2:
        return "HIGH"
    if income >= 10000 or risky:
        return "MEDIUM"
    return "LOW"


@transaction.atomic
def submit_application(app: AccountApplication, idempotency_key: str = "") -> AccountApplication:
    """Validate completeness, mark SUBMITTED, queue manager review. Idempotent."""
    if app.status != ApplicationStatus.DRAFT:
        return app  # idempotent re-submission returns the same application
    missing = []
    for idx, (_, fields) in enumerate(STEPS[:7]):
        err = validate_step(idx, app.data)
        if err:
            missing.append(err)
    if missing:
        raise ApplicationError(missing[0])
    # duplicate applicant: existing customer with this email
    if User.objects.filter(email__iexact=app.email,
                           role__in=("CUSTOMER", "PREMIUM_CUSTOMER")).exists():
        raise ApplicationError("DUPLICATE_CUSTOMER")
    other = AccountApplication.objects.filter(email__iexact=app.email,
                                              status__in=[ApplicationStatus.SUBMITTED,
                                                          ApplicationStatus.IDENTITY_REVIEW,
                                                          ApplicationStatus.KYC_REVIEW,
                                                          ApplicationStatus.UNDER_REVIEW]).exclude(pk=app.pk)
    if other.exists():
        raise ApplicationError("DUPLICATE_APPLICATION")
    app.status = ApplicationStatus.SUBMITTED
    app.risk_level = _risk_level(app)
    app.submitted_at = timezone.now()
    app.idempotency_key = idempotency_key or f"portal-{app.reference}"
    app.current_step = TOTAL_STEPS
    app.save()

    # queue human review in the existing approvals engine
    from apps.managerops.models import ApprovalRequest, ManagerLevel
    from apps.managerops.authority import can_approve  # noqa: F401 (policy source)
    required = ManagerLevel.BRANCH if app.risk_level != "LOW" else ManagerLevel.RELATIONSHIP
    ApprovalRequest.objects.create(
        operation_type="ONBOARDING_REVIEW",
        resource_type="portal.AccountApplication",
        resource_id=str(app.pk),
        requested_by=None,
        required_level=required,
        reason=f"Public onboarding {app.reference} — risk {app.risk_level}",
        payload={"reference": app.reference},
    )
    app.status = ApplicationStatus.IDENTITY_REVIEW
    app.save(update_fields=["status"])
    audit(action="PORTAL_APPLICATION_SUBMITTED", metadata={"reference": app.reference,
                                                           "risk": app.risk_level})
    return app


@transaction.atomic
def decide_application(req, approver, approve: bool, reason=""):
    """Called from managerops.decide_approval after authority checks pass."""
    from apps.managerops.services import ApprovalError

    ref = req.payload.get("reference")
    app = AccountApplication.objects.select_for_update().get(reference=ref)
    if app.status not in (ApplicationStatus.SUBMITTED, ApplicationStatus.IDENTITY_REVIEW,
                          ApplicationStatus.UNDER_REVIEW, ApplicationStatus.KYC_REVIEW):
        raise ApprovalError("NOT_PENDING")
    # respect the escalation level chosen at submission time
    from apps.managerops.access import get_manager_profile
    from apps.managerops.models import LEVEL_ORDER

    prof = get_manager_profile(approver)
    if prof.rank < LEVEL_ORDER[req.required_level]:
        raise ApprovalError("INSUFFICIENT_AUTHORITY")

    app.reviewed_by = approver
    app.decided_at = timezone.now()
    if not approve:
        app.status = ApplicationStatus.REJECTED
        app.decision_reason = reason or "Application declined during review."
        app.save()
        audit(actor=approver, action="PORTAL_APPLICATION_REJECTED", resource=app)
        return app

    if reason:
        # request additional information instead of approving outright
        app.status = ApplicationStatus.ADDITIONAL_INFORMATION_REQUIRED
        app.decision_reason = reason
        app.save()
        audit(actor=approver, action="PORTAL_APPLICATION_INFO_REQUESTED", resource=app)
        return app

    # approve: create the customer through the standard onboarding domain
    from apps.customers.models import Customer
    from apps.managerops.models import BankBranch
    from apps.managerops.services import open_account_application

    if User.objects.filter(email__iexact=app.email).exists():
        raise ApprovalError("DUPLICATE_CUSTOMER")
    branch = BankBranch.objects.first()
    names = app.full_name.rsplit(" ", 1)
    user = User.objects.create_user(
        username=f"{names[0].lower()}.{names[-1].lower()}{secrets.token_hex(3)}",
        email=app.email,
        phone=app.data.get("phone", ""),
        first_name=names[0], last_name=names[-1] if len(names) > 1 else "",
        role="PREMIUM_CUSTOMER" if "PREMIUM" in app.products else "CUSTOMER",
    )
    # Demo delivery: temporary credentials are stored on the application and
    # shown once on the application status page (production would email them).
    temp_password = "Bankio-" + secrets.token_urlsafe(9)
    user.set_password(temp_password)
    app.temp_password = temp_password
    user.save()
    Customer.objects.create(user=user,
                            customer_number=f"CUST-{secrets.token_hex(4).upper()}",
                            branch=branch,
                            monthly_income=app.data.get("monthly_income") or None,
                            occupation=app.data.get("occupation", ""),
                            address=app.data.get("address", ""),
                            assigned_manager=approver)
    KYCReview.objects.create(customer=user, status="APPROVED", risk_level=app.risk_level,
                             reviewed_by=approver, notes="Digital onboarding identity verification")
    product = "SAVINGS" if "SAVINGS" in app.products else "CHECKING"
    app_obj = open_account_application(manager=approver, customer_id=user.pk, product_type=product)
    app.customer = user
    if app_obj is not None and app_obj.state == "ACTIVE":
        app.status = ApplicationStatus.ACCOUNT_CREATED
    else:
        app.status = ApplicationStatus.KYC_REVIEW
    app.save()
    audit(actor=approver, action="PORTAL_APPLICATION_APPROVED", resource=app,
          metadata={"customer": user.username})
    return app


def kyc_pending(user) -> bool:
    latest = user.kyc_reviews.order_by("-created_at").first()
    return bool(latest and latest.status == "PENDING")


def loan_simulation(amount: float, months: int, annual_rate: float = 12.9) -> dict:
    """Non-binding monthly payment estimate (annuity formula), marketing only."""
    if amount <= 0 or months <= 0:
        raise ApplicationError("INVALID_SIMULATION")
    r = annual_rate / 100 / 12
    payment = amount * r / (1 - (1 + r) ** (-months)) if r else amount / months
    return {"payment": round(payment, 2), "total": round(payment * months, 2),
            "amount": amount, "months": months}
