"""Manager portal views. Views validate HTTP + authorize; logic lives in services."""
import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Account, AccountStatus
from apps.audit.services import record as audit
from apps.compliance.models import FraudAlert, KYCReview
from apps.identity.models import Role, User
from apps.transfers.services import TransferError, execute_transfer  # noqa: F401 (context)
from apps.transfers.models import Transfer

from . import services
from .access import assert_customer_access, get_manager_profile, visible_customers
from .authority import can_approve
from .models import (
    AccountApplication,
    AccountRestriction,
    ApprovalRequest,
    Appointment,
    BankBranch,
    ManagerNote,
    ServiceRequest,
)


def _ctx(request):
    profile = get_manager_profile(request.user)
    return profile


@login_required
def manager_dashboard(request):
    profile = _ctx(request)
    customers = visible_customers(profile)
    accounts = Account.objects.filter(customer__in=customers.values("pk"))
    deposits = sum((a.current_balance for a in accounts), Decimal("0"))
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    ctx = {
        "profile": profile,
        "managed_count": customers.count(),
        "total_assets": deposits,
        "opened_this_month": Account.objects.filter(
            customer__in=customers.values("pk"), created_at__gte=month_start).count(),
        "pending_openings": AccountApplication.objects.exclude(state__in=["ACTIVE", "REJECTED", "CANCELED"]).count(),
        "pending_approvals": ApprovalRequest.objects.filter(status="PENDING").count(),
        "pending_kyc": KYCReview.objects.filter(status="PENDING").count(),
        "restricted_accounts": AccountRestriction.objects.filter(active=True).count(),
        "high_risk": customers.filter(user__kyc_reviews__risk_level="HIGH").distinct().count(),
        "fraud_alerts": FraudAlert.objects.filter(resolved=False).count(),
        "recent_activity": request.user.audit_events.all()[:8],
        "appointments": Appointment.objects.filter(manager=request.user, completed=False, canceled=False)[:5],
        "branches": BankBranch.objects.all(),
    }

    # first-access tutorial (staff variant): server-side state, role-scoped steps
    from apps.identity import tour as tour_mod

    show_tour, _progress = tour_mod.tour_state(request.user, request)
    if show_tour:
        ctx["tour_steps"] = json.dumps(tour_mod.staff_steps(profile.get_level_display()))
        ctx["show_tour"] = True
        tour_mod.consume_replay(request)
    else:
        ctx["show_tour"] = False
    return render(request, "manager/dashboard.html", ctx)


@login_required
def customer_search(request):
    profile = _ctx(request)
    q = (request.GET.get("q") or "").strip()
    qs = visible_customers(profile)
    if q:
        # visible_customers yields Customer rows: search through the related user
        qs = qs.filter(
            Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q) |
            Q(user__email__iexact=q) | Q(user__phone__icontains=q) |
            Q(user__username__iexact=q) | Q(customer_number__iexact=q) |
            Q(user__accounts__account_number__iexact=q)
        ).distinct()
    results = [
        {
            "user": c.user,
            "customer_number": c.customer_number,
            "name": c.user.get_full_name(),
            "email_masked": _mask_email(c.user.email),
            "phone_masked": _mask_phone(c.user.phone),
        }
        for c in qs.select_related("user")[:20]
    ]
    return render(request, "manager/search_results.html" if request.headers.get("HX-Request")
                  else "manager/customers.html", {"results": results, "q": q})


def _mask_email(email):
    name, _, domain = email.partition("@")
    return f"{name[:2]}•••@{domain}" if name else "•••"


def _mask_phone(phone):
    return f"••••{phone[-4:]}" if len(phone) >= 4 else "••••"


@login_required
def customer_360(request, customer_id):
    profile = _ctx(request)
    user = get_object_or_404(User, pk=customer_id, role__in=(Role.CUSTOMER, Role.PREMIUM_CUSTOMER))
    assert_customer_access(profile, user.customer_profile)

    accounts = Account.objects.filter(customer=user)
    kyc = user.kyc_reviews.order_by("-created_at").first()
    positions = user.positions.all()
    invested = sum(p.market_value for p in positions)
    credit_cards = [a for a in accounts if a.cards.exists()]
    ctx = {
        "c": user,
        "profile_row": user.customer_profile,
        "accounts": accounts,
        "kyc": kyc,
        "kyc_status": services.kyc_status(user),
        "positions": positions,
        "invested": invested,
        "deposits": sum((a.current_balance for a in accounts), Decimal("0")),
        "notes": user.manager_notes.order_by("-created_at")[:10],
        "service_requests": user.service_requests.order_by("-created_at")[:10],
        "appointments": user.customer_appointments.order_by("-scheduled_at")[:5],
        "restrictions": AccountRestriction.objects.filter(account__customer=user),
        "fraud_alerts": user.fraud_alerts.order_by("-created_at")[:5],
        "loans": user.loan_applications.all(),
        "transfers": Transfer.objects.filter(source_account__customer=user)[:8],
        "can": {op: can_approve(profile, op, Decimal("999999999999")).allowed for op in []},
    }
    return render(request, "manager/customer360.html", ctx)


@require_POST
@login_required
def onboard_customer(request):
    profile = _ctx(request)
    data = {
        "full_name": request.POST.get("full_name", ""),
        "date_of_birth": request.POST.get("date_of_birth", ""),
        "email": request.POST.get("email", ""),
        "phone": request.POST.get("phone", ""),
        "address": request.POST.get("address", ""),
        "occupation": request.POST.get("occupation", ""),
        "segment": request.POST.get("segment", "RETAIL"),
    }
    try:
        user = services.create_customer(manager=profile.user, data=data)
    except services.OnboardingError as e:
        code = str(e)
        if code.startswith("POSSIBLE_EXISTING_CUSTOMER"):
            return render(request, "manager/_onboard_error.html",
                          {"error": "Possible existing customer detected — verify before creating."},
                          status=409)
        return render(request, "manager/_onboard_error.html", {"error": code}, status=400)
    messages.success(request, f"Customer {user.username} created — complete KYC to open accounts.")
    return redirect("manager_customer360", customer_id=user.pk)


@require_POST
@login_required
def open_account(request):
    profile = _ctx(request)
    try:
        app = services.open_account_application(
            manager=request.user,
            customer_id=request.POST["customer_id"],
            product_type=request.POST.get("product_type", "CHECKING"),
            currency=request.POST.get("currency", "USD"),
        )
    except (ValueError, PermissionDenied) as e:
        return render(request, "manager/_open_account_error.html", {"error": str(e)}, status=400)
    if request.headers.get("HX-Request"):
        return render(request, "manager/_application_result.html", {"app": app})
    return redirect("manager_customer360", customer_id=app.customer_id)


@require_POST
@login_required
def approve_request(request):
    _ctx(request)
    try:
        req = services.decide_approval(
            approver=request.user, approval_id=request.POST["approval_id"],
            approve=request.POST.get("decision") == "approve",
            rejection_reason=request.POST.get("rejection_reason", ""),
        )
    except services.ApprovalError as e:
        return render(request, "manager/_approval_error.html", {"error": e.code}, status=403)
    return redirect("manager_approvals")


@require_POST
@login_required
def apply_restriction(request):
    try:
        services.request_restriction(
            manager=request.user, account_id=request.POST["account_id"],
            restriction_type=request.POST.get("restriction_type"),
            reason=request.POST.get("reason", "manager request"),
        )
    except services.RestrictionError as e:
        return render(request, "manager/_restriction_error.html", {"error": e.code}, status=403)
    return redirect("manager_customer360", customer_id=request.POST["customer_id"])


@require_POST
@login_required
def lift_restriction_view(request):
    profile = _ctx(request)
    is_compliance = False  # managers cannot lift AML/legal holds; compliance portal does that
    try:
        r = services.lift_restriction(actor=request.user,
                                      restriction_id=request.POST["restriction_id"],
                                      is_compliance=is_compliance)
    except services.RestrictionError as e:
        return render(request, "manager/_restriction_error.html", {"error": e.code}, status=403)
    return redirect("manager_customer360", customer_id=r.account.customer_id)


@login_required
def approvals_queue(request):
    _ctx(request)
    pending = ApprovalRequest.objects.filter(status="PENDING").select_related("requested_by").order_by("-created_at")
    decided = ApprovalRequest.objects.exclude(status="PENDING")[:15]
    return render(request, "manager/approvals.html", {"pending": pending, "decided": decided})


@login_required
def restrictions_view(request):
    profile = _ctx(request)
    active = (AccountRestriction.objects
              .filter(active=True, account__customer__in=visible_customers(profile).values("user_id"))
              .select_related("account__customer", "requested_by", "approved_by")
              .order_by("-effective_at"))
    return render(request, "manager/restrictions.html", {"active": active})


@login_required
def add_note(request, customer_id):
    profile = _ctx(request)
    user = get_object_or_404(User, pk=customer_id)
    assert_customer_access(profile, user.customer_profile)
    if request.method == "POST":
        ManagerNote.objects.create(customer=user, manager=request.user,
                                   category=request.POST.get("category", "GENERAL"),
                                   note=request.POST.get("note", ""))
        audit(actor=request.user, action="CUSTOMER_NOTE_ADDED", resource=user)
    return redirect("manager_customer360", customer_id=customer_id)


@login_required
def create_service_request(request, customer_id):
    profile = _ctx(request)
    user = get_object_or_404(User, pk=customer_id)
    assert_customer_access(profile, user.customer_profile)
    if request.method == "POST":
        ServiceRequest.objects.create(
            customer=user, request_type=request.POST.get("request_type"),
            detail=request.POST.get("detail", ""), opened_by=request.user,
        )
        audit(actor=request.user, action="SERVICE_REQUEST_CREATED", resource=user)
    return redirect("manager_customer360", customer_id=customer_id)


# ------------------------------------------------------------ card requests
@login_required
@login_required
def funding_view(request):
    """GET: funding form + recent funding journals. POST: execute idempotent funding."""
    profile = _ctx(request)
    from apps.accounts.services import FundingError, fund_account
    from apps.ledger.models import JournalEntry

    accounts = Account.objects.filter(
        customer__in=visible_customers(profile).values("user_id"),
        status=AccountStatus.ACTIVE,
    ).select_related("customer").order_by("account_number")
    recent = (JournalEntry.objects.filter(reference__startswith="FUND-")
              .order_by("-posted_at")[:10])
    if request.method == "POST":
        err = None
        try:
            result = fund_account(
                manager=request.user,
                account_id=request.POST.get("account"),
                amount=request.POST.get("amount"),
                reason=request.POST.get("reason", ""),
                external_ref=request.POST.get("external_ref", ""),
                idempotency_key=request.POST.get("idempotency_key", ""),
            )
            if result["replayed"]:
                messages.info(request, "Funding already processed (replay ignored).")
            else:
                messages.success(request, f"Funding posted as {result['journal'].reference}.")
        except FundingError as e:
            err = {"INVALID_AMOUNT": "Amount must be a positive number.",
                   "ACCOUNT_NOT_FOUND": "Select a valid account.",
                   "ACCOUNT_NOT_ACTIVE": "Account is not active."}.get(e.code, e.code)
        if err:
            messages.error(request, err)
        return redirect("manager_funding")
    return render(request, "manager/funding.html", {"profile": profile, "accounts": accounts, "recent": recent})


def card_requests_view(request):
    if request.user.role != "MANAGER":
        raise PermissionDenied("Manager role required")
    from apps.cards.models import CardRequest

    pending = CardRequest.objects.filter(status="PENDING").select_related("customer", "account")
    decided = CardRequest.objects.exclude(status="PENDING").select_related("customer", "reviewed_by")[:15]
    return render(request, "manager/card_requests.html", {
        "profile": _ctx(request), "pending": pending, "decided": decided,
    })


@login_required
@require_POST
def decide_card_request(request, req_id: int):
    if request.user.role != "MANAGER":
        raise PermissionDenied("Manager role required")
    from apps.cards.models import CardRequest
    from apps.cards.services import CardRequestError, decide_card_request

    req = get_object_or_404(CardRequest, pk=req_id)
    approve = request.POST.get("decision") == "approve"
    limit_raw = (request.POST.get("approved_limit") or "").strip()
    try:
        decide_card_request(
            req, request.user, approve,
            approved_limit=Decimal(limit_raw) if limit_raw else None,
            reason=request.POST.get("reason", ""),
        )
        messages.success(request, f"Card request #{req.pk} {'approved' if approve else 'rejected'}.")
    except (CardRequestError, PermissionDenied) as e:
        code = getattr(e, "code", str(e))
        messages.error(request, str(code).replace("_", " ").title())
    return redirect("manager_card_requests")
