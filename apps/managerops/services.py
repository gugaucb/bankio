"""Manager domain services: onboarding, account opening, restrictions, approvals.

Rules enforced here (never in views/templates):
  - duplicate customer detection before creation
  - KYC gating for account activation
  - server-side unique account number generation
  - maker-checker: requester can never approve
  - AML/legal holds only liftable by compliance role
"""
import uuid
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Account
from apps.audit.services import record as audit
from apps.compliance.models import KYCReview
from apps.customers.models import Customer
from apps.identity.models import User
from apps.ledger import services as ledger

from .access import assert_customer_access
from .authority import AuthorityError, can_approve
from .models import (
    AccountApplication,
    AccountRestriction,
    ApprovalRequest,
    BankBranch,
    ManagerLevel,
)

# ---------------------------------------------------------------- onboarding


class OnboardingError(Exception):
    pass


def find_duplicates(email="", phone=""):
    """Return existing customer users matching known identifiers (duplicate detection)."""
    q = Q()
    if email:
        q |= Q(email__iexact=email)
    if phone:
        q |= Q(phone=phone)
    if not q:
        return User.objects.none()
    return User.objects.filter(q, role__in=("CUSTOMER", "PREMIUM_CUSTOMER"))


@transaction.atomic
def create_customer(*, manager, data):
    """Create a bank customer (User + Customer profile). Duplicate detection first."""
    hit = find_duplicates(email=data.get("email", ""), phone=data.get("phone", "")).first()
    if hit:
        raise OnboardingError(f"POSSIBLE_EXISTING_CUSTOMER:{hit.username}")
    if not data.get("email") or not data.get("full_name") or not data.get("date_of_birth"):
        raise OnboardingError("MISSING_REQUIRED_FIELDS")
    if not data.get("full_name") or not data.get("email") or not data.get("date_of_birth"):
        raise OnboardingError("MISSING_REQUIRED_FIELDS")

    from datetime import datetime

    dob = datetime.strptime(str(data["date_of_birth"]), "%Y-%m-%d").date()
    age = (date.today() - dob).days // 365
    if age < 18:
        raise OnboardingError("UNDERAGE")

    names = data["full_name"].rsplit(" ", 1)
    user = User.objects.create_user(
        username=data.get("username") or f"{names[0].lower()}.{names[-1].lower()}{uuid.uuid4().hex[:4]}",
        email=data["email"], phone=data.get("phone", ""),
        first_name=names[0], last_name=names[-1] if len(names) > 1 else "",
        role="PREMIUM_CUSTOMER" if data.get("customer_type") == "PREMIUM" else "CUSTOMER",
    )
    user.set_password(data.get("password", uuid.uuid4().hex))
    user.save()
    customer = Customer.objects.create(
        user=user,
        customer_number=f"CUST-{uuid.uuid4().hex[:8].upper()}",
        segment=data.get("segment", "RETAIL"),
        branch=manager.manager_profile.branch if hasattr(manager, "manager_profile") else None,
        assigned_manager=manager,
    )
    KYCReview.objects.create(customer=user, status="PENDING")
    audit(actor=manager, action="CUSTOMER_CREATED", resource=user,
          metadata={"by_branch": (manager.manager_profile.branch.branch_code
                                   if hasattr(manager, "manager_profile") and manager.manager_profile.branch else "")})
    return user


# ---------------------------------------------------------- account opening


def kyc_status(user) -> str:
    latest = user.kyc_reviews.order_by("-created_at").first()
    return latest.status if latest else "KYC_NOT_STARTED"


@transaction.atomic
def open_account_application(*, manager, customer_id, product_type, currency="USD",
                             joint_with=None, business=None):
    from .access import get_manager_profile

    prof = get_manager_profile(manager)
    customer = User.objects.get(pk=customer_id, role__in=("CUSTOMER", "PREMIUM_CUSTOMER"))
    assert_customer_access(prof, customer.customer_profile)

    if customer.customer_profile.status == "RESTRICTED":
        raise ValueError("CUSTOMER_RESTRICTED")

    app = AccountApplication.objects.create(
        customer=customer, product_type=product_type, currency=currency,
        requested_by=manager, branch=prof.branch,
        business_name=(business or {}).get("legal_name", ""),
        registration_number=(business or {}).get("registration_number", ""),
    )

    # joint accounts: every owner needs verified KYC
    owners = [customer] + list(User.objects.filter(pk__in=[u.pk for u in joint_with or []]))
    kyc_ok = all(kyc_status(o) == "APPROVED" for o in owners)
    if not kyc_ok:
        app.transition("PENDING_KYC")
        audit(actor=manager, action="ACCOUNT_APPLICATION_CREATED", resource=app, metadata={"state": "PENDING_KYC"})
        return app

    # high-value / business products need branch-level approval
    decision = can_approve(prof, "ACCOUNT_OPENING",
                           Decimal("1") if product_type in ("JOINT", "BUSINESS") else None)
    if product_type in ("JOINT", "BUSINESS") and prof.rank < 2:
        app.transition("PENDING_APPROVAL")
        ApprovalRequest.objects.create(
            operation_type="ACCOUNT_OPENING", resource_type="AccountApplication",
            resource_id=str(app.pk), requested_by=manager,
            required_level=decision.required_level or ManagerLevel.BRANCH,
            reason=f"{product_type} account requires branch approval",
        )
        return app

    activate_application(app, actor=manager)
    return app


def _next_account_number(branch_code: str) -> str:
    """Serialized per-branch counter; check digit appended; DB unique constraint backstop."""
    from .models import AccountNumberCounter

    counter, _ = AccountNumberCounter.objects.select_for_update().get_or_create(
        branch_code=branch_code, defaults={"last": 100000000}
    )
    counter.last += 1
    base = f"{branch_code}{counter.last}"
    check = str((sum(int(d) * w for d, w in zip(base, [3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1])) % 10))
    counter.save(update_fields=["last"])
    return base + check


@transaction.atomic
def activate_application(app: AccountApplication, actor):
    if app.state == "PENDING_KYC" and kyc_status(app.customer) != "APPROVED":
        raise ValueError("KYC_PENDING")
    if app.state == "ACTIVE":
        return app.account  # idempotent
    if app.state not in ("APPLICATION", "PENDING_APPROVAL", "APPROVED"):
        raise ValueError(f"Illegal activation from {app.state}")
    if app.state != "APPROVED":
        app.transition("APPROVED")
    app.transition("ACTIVE")

    number = _next_account_number(app.branch.branch_code if app.branch else "0001")
    la = ledger.get_or_create_account(f"2001-{number}", f"Account {number}", is_customer=True)
    account = Account.objects.create(
        customer=app.customer, account_number=number, type=app.product_type,
        currency=app.currency, ledger_account=la,
    )
    for extra in app.joint_with.all():
        pass  # joint ownership recorded via application record (single-owner projection)
    app.account = account
    app.save(update_fields=["account"])
    audit(actor=actor, action="ACCOUNT_OPENED", resource=account,
          metadata={"application": app.pk})
    return account


# ------------------------------------------------------------- restrictions


class RestrictionError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


RESTRICTION_MIN_RANK = {"AML_HOLD": 99, "LEGAL_HOLD": 99}  # managers never; compliance only


@transaction.atomic
def request_restriction(*, manager, account_id, restriction_type, reason, require_approval_above_rank=2):
    from .access import get_manager_profile

    prof = get_manager_profile(manager)
    account = Account.objects.get(pk=account_id)
    assert_customer_access(prof, account.customer.customer_profile)
    if restriction_type in RESTRICTION_MIN_RANK and RESTRICTION_MIN_RANK[restriction_type] > 4:
        raise RestrictionError("COMPLIANCE_ONLY_RESTRICTION")
    existing = AccountRestriction.objects.filter(account=account, restriction_type=restriction_type, active=True).exists()
    if existing:
        raise RestrictionError("DUPLICATE_RESTRICTION")
    r = AccountRestriction.objects.create(
        account=account, restriction_type=restriction_type, reason=reason,
        requested_by=manager,
    )
    audit(actor=manager, action="ACCOUNT_RESTRICTED", resource=account,
          metadata={"type": restriction_type, "reason": reason})
    return r


@transaction.atomic
def lift_restriction(*, actor, restriction_id, is_compliance=False):
    r = AccountRestriction.objects.select_for_update().get(pk=restriction_id)
    if not r.active:
        raise RestrictionError("ALREADY_INACTIVE")
    if r.restriction_type in AccountRestriction.COMPLIANCE_ONLY and not is_compliance:
        raise RestrictionError("COMPLIANCE_ONLY")
    r.active = False
    r.approved_by = actor
    r.save(update_fields=["active", "approved_by"])
    audit(actor=actor, action="ACCOUNT_UNBLOCKED", resource=r.account, metadata={"type": r.restriction_type})
    return r


# ---------------------------------------------------------------- approvals


class ApprovalError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@transaction.atomic
def submit_approval_request(*, manager, operation_type, amount=None, resource=None, reason="", payload=None):
    from .access import get_manager_profile

    prof = get_manager_profile(manager)
    decision = can_approve(prof, operation_type, amount)
    if decision.allowed:
        return None  # within authority — caller may execute directly
    req = ApprovalRequest.objects.create(
        operation_type=operation_type,
        resource_type=resource.__class__.__name__ if resource is not None else "",
        resource_id=str(getattr(resource, "pk", "") or ""),
        requested_by=manager,
        required_level=decision.required_level or ManagerLevel.REGIONAL,
        amount=amount, reason=reason or decision.reason, payload=payload or {},
    )
    audit(actor=manager, action="APPROVAL_REQUESTED", resource=req,
          metadata={"operation": operation_type, "amount": str(amount)})
    return req


@transaction.atomic
def decide_approval(*, approver, approval_id, approve: bool, rejection_reason=""):
    from .access import get_manager_profile

    prof = get_manager_profile(approver)
    req = ApprovalRequest.objects.select_for_update(of=("self",)).select_related("requested_by").get(pk=approval_id)
    if req.status != "PENDING":
        raise ApprovalError("NOT_PENDING")
    if req.requested_by_id == approver.pk:
        raise ApprovalError("DENIED_SELF_APPROVAL")
    decision = can_approve(prof, req.operation_type, req.amount)
    if not decision.allowed:
        raise ApprovalError("INSUFFICIENT_AUTHORITY")
    req.reviewed_by = approver
    req.reviewed_at = timezone.now()
    req.status = "APPROVED" if approve else "REJECTED"
    if not approve and rejection_reason:
        req.reason = rejection_reason
    req.save(update_fields=["reviewed_by", "reviewed_at", "status", "reason"])
    audit(actor=approver, action=f"APPROVAL_{req.status}", resource=req,
          metadata={"operation": req.operation_type})

    if approve and req.operation_type == "LIMIT_INCREASE" and req.resource_type == "Account":
        account = Account.objects.select_for_update().get(pk=req.resource_id)
        account.tx_limit = req.amount
        account.save(update_fields=["tx_limit"])

    if approve and req.operation_type == "ACCOUNT_OPENING" and req.resource_type == "AccountApplication":
        app = AccountApplication.objects.get(pk=req.resource_id)
        if kyc_status(app.customer) != "APPROVED":
            app.transition("PENDING_KYC")
        else:
            activate_application(app, actor=approver)

    if req.operation_type == "ONBOARDING_REVIEW":
        from apps.portal.services import decide_application

        decide_application(req, approver, approve, rejection_reason)
    return req


# ------------------------------------------------------- limit change flow


@transaction.atomic
def request_limit_change(*, manager, account_id, new_limit, reason=""):
    from .access import get_manager_profile

    prof = get_manager_profile(manager)
    account = Account.objects.select_for_update().get(pk=account_id)
    assert_customer_access(prof, account.customer.customer_profile)
    new_limit = Decimal(str(new_limit))
    if new_limit <= 0:
        raise ValueError("INVALID_LIMIT")
    decision = can_approve(prof, "LIMIT_INCREASE", new_limit)
    if decision.allowed:
        account.tx_limit = new_limit
        account.save(update_fields=["tx_limit"])
        audit(actor=manager, action="LIMIT_CHANGE_APPROVED", resource=account,
              metadata={"new_limit": str(new_limit)})
        return {"applied": True}
    req = ApprovalRequest.objects.create(
        operation_type="LIMIT_INCREASE", resource_type="Account",
        resource_id=str(account.pk), requested_by=manager,
        required_level=decision.required_level, amount=new_limit,
        current_value=account.tx_limit, reason=reason or decision.reason,
    )
    return {"applied": False, "request": req, "required_level": decision.required_level}


# ------------------------------------------------------------ account closure


@transaction.atomic
def request_account_closure(*, manager, account_id, reason=""):
    from .access import get_manager_profile

    prof = get_manager_profile(manager)
    account = Account.objects.select_for_update().get(pk=account_id)
    assert_customer_access(prof, account.customer.customer_profile)
    if account.current_balance != Decimal("0"):
        raise ValueError("BALANCE_NOT_ZERO")
    if account.restrictions.filter(active=True).exists():
        raise ValueError("ACTIVE_RESTRICTION")
    if account.status == "CLOSED":
        raise ValueError("ALREADY_CLOSED")
    account.status = "CLOSED"
    account.save(update_fields=["status"])
    audit(actor=manager, action="ACCOUNT_CLOSURE_REQUESTED", resource=account,
          metadata={"reason": reason, "approved_inline": True})
    return account
