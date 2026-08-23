"""Public portal views: marketing pages, account opening wizard, application status,
and the dedicated manager login. Views validate HTTP; rules live in services."""
from django.contrib import messages
from django.contrib.auth import login, logout
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render

from apps.audit.services import record as audit
from apps.identity.services import attempt_login, LoginLocked
from apps.identity.models import User
from apps.managerops.models import ApprovalRequest

from .models import PRODUCTS, ApplicationStatus
from .services import (ApplicationError, STEPS, TOTAL_STEPS, decide_application,
                       get_or_resume, loan_simulation, save_step, start_application,
                       submit_application)


# ------------------------------------------------------------- public pages

def home(request):
    return render(request, "site/home.html")


def _page(headline, intro, features, cta_title="Get started with Bankio", extra=None,
          title=None, template="site/generic.html"):
    def view(request):
        return render(request, template, {
            "page_title": title or headline, "headline": headline, "intro": intro,
            "features": features, "cta_title": cta_title, "extra": extra,
        })
    return view


personal = _page(
    "Personal Banking",
    "Everyday money management with a digital account: transfers, payments, savings goals and insights.",
    [
        {"title": "Digital Account", "items": ["Everyday banking", "Transfers & payments", "Financial organization"]},
        {"title": "Savings Goals", "items": ["Automatic savings", "Goal tracking"]},
        {"title": "Insights", "items": ["Spending analytics", "Budget overview"]},
        {"title": "Benefits", "items": ["No hidden fees on the demo tier", "Instant transfers between customers", "Real-time notifications"]},
    ],
)
business = _page(
    "Bankio for Business",
    "Business accounts with payment management, company cards and cash-flow visibility for your team.",
    [
        {"title": "Business Accounts", "items": ["Separate business profile", "Cash-flow visibility"]},
        {"title": "Payments & Controls", "items": ["Payment management", "Employee spending controls"]},
        {"title": "Financing", "items": ["Business lending products", "Non-binding simulations"]},
    ],
    cta_title="Open a business account",
)
cards_page = _page(
    "Bankio Cards",
    "Debit, credit, premium and virtual cards with instant freeze and real-time controls.",
    [
        {"title": "Bankio Debit", "items": ["Everyday purchases", "ATM access", "Real-time notifications"]},
        {"title": "Bankio Credit", "items": ["Credit line", "Spending controls", "Transaction alerts"]},
        {"title": "Virtual Cards", "items": ["One-time numbers for online payments", "Freeze instantly", "Per-merchant limits"]},
        {"title": "Card Controls", "items": ["Instant freeze from the dashboard", "International usage controls", "Per-card limits"]},
    ],
)
investments_page = _page(
    "Investments",
    "Track your portfolio, explore investment products and follow performance over time. Investments involve risk; returns are never guaranteed.",
    [
        {"title": "Portfolio Visualization", "items": ["Positions and market value", "Performance overview"]},
        {"title": "Investment Access", "items": ["Stocks, ETFs and bonds", "Instrument catalog"]},
        {"title": "Goal Integration", "items": ["Tie investments to savings goals", "Track progress in the dashboard"]},
    ],
    cta_title="Explore investing with Bankio",
)
security_page = _page(
    "Security at Bankio",
    "Security capabilities designed to protect your account — without exposing implementation details that could aid abuse.",
    [
        {"title": "Account Protection", "items": ["Multi-factor authentication", "Login lockout after repeated failures", "Trusted device recognition"]},
        {"title": "Transaction Safety", "items": ["Transaction verification", "Fraud monitoring rules", "Transfer limits"]},
        {"title": "Card Safety", "items": ["Instant card freeze", "Card spending controls", "Real-time decline alerts"]},
        {"title": "Sessions", "items": ["Secure sessions", "Device management", "Full audit trail of sensitive actions"]},
    ],
    cta_title="Your money, protected",
)
help_page = _page(
    "Help Center",
    "Find answers about accounts, transfers, cards, payments, security, loans and investments.",
    [
        {"title": "How do I open an account?", "items": ["Click Open Your Account", "Complete the short application", "Track it online with your reference"]},
        {"title": "How long does account review take?", "items": ["Applications enter review immediately", "Status updates appear under Application Status"]},
        {"title": "How do I access my account?", "items": ["Use Sign In with email/username + password", "MFA when enabled", "You land on your dashboard"]},
        {"title": "How do I freeze my card?", "items": ["Open your dashboard → Cards", "Use instant freeze"]},
        {"title": "How do transfers work?", "items": ["Pick a source account and beneficiary", "Limits apply per transaction and per day", "Transfers settle instantly"]},
        {"title": "How do I contact support?", "items": ["Authenticated customers can create support requests", "Requests are handled by bank staff"]},
        {"title": "How do I track my application?", "items": ["Keep your application reference (BNK-APP-…)", "Visit Application Status anytime"]},
    ],
    cta_title="Still need help?",
)
loans_page = _page(
    "Loans",
    "Personal, auto and mortgage lending with transparent, non-binding simulations.",
    [
        {"title": "Personal Loan", "items": ["Flexible terms", "Simulate before applying"]},
        {"title": "Auto Loan", "items": ["Vehicle financing", "Fixed schedules"]},
        {"title": "Mortgage", "items": ["Home financing", "Long-term planning"]},
    ],
    cta_title="Ready to explore a loan?",
)


def loan_simulate(request):
    """Non-binding loan simulation (JSON)."""
    try:
        amount = float(request.GET.get("amount") or request.POST.get("amount") or 0)
        months = int(request.GET.get("months") or request.POST.get("months") or 0)
        result = loan_simulation(amount, months)
    except (TypeError, ValueError, ApplicationError):
        return JsonResponse({"error": "INVALID_SIMULATION"}, status=400)
    return JsonResponse(result)


# ------------------------------------------------------- account opening wizard

STEP_LABELS = {
    0: ("PERSONAL_INFORMATION", "Personal information"),
    1: ("CONTACT_INFORMATION", "Contact information"),
    2: ("ADDRESS", "Address"),
    3: ("IDENTITY", "Identity"),
    4: ("EMPLOYMENT_OCCUPATION", "Employment / occupation"),
    5: ("INCOME", "Income"),
    6: ("TAX_INFORMATION", "Tax information"),
    7: ("PRODUCT_SELECTION", "Product selection"),
    8: ("CONSENTS", "Review & consents"),
}


def open_account_landing(request):
    if request.method == "POST":
        app = start_application()
        request.session["portal_application"] = app.reference
        audit(action="PORTAL_APPLICATION_STARTED", resource=app)
        return redirect("portal_wizard_step", step=1)
    return render(request, "site/open_account.html")


def _current_app(request):
    ref = request.session.get("portal_application")
    if not ref:
        return None
    from .models import AccountApplication

    try:
        return AccountApplication.objects.get(reference=ref,
                                              status=ApplicationStatus.DRAFT)
    except AccountApplication.DoesNotExist:
        return None


def wizard(request, step: int = 1):
    app = _current_app(request)
    if app is None:
        return redirect("portal_open_account")
    step = max(1, min(step, TOTAL_STEPS))
    idx = step - 1
    key, label = STEP_LABELS[idx]
    error = request.session.pop("wizard_error", "")
    ctx = {
        "app": app, "step": step, "total": TOTAL_STEPS, "step_key": key,
        "step_label": label, "fields": STEPS[idx][1], "products": PRODUCTS,
        "data": app.data, "error": error,
        "funds_options": ["SALARY", "BUSINESS", "INVESTMENTS", "SAVINGS", "OTHER"],
        "employment_options": ["EMPLOYED", "SELF_EMPLOYED", "STUDENT", "RETIRED", "UNEMPLOYED"],
    }
    if step == TOTAL_STEPS:
        labels = [("Full name", "full_name"), ("Date of birth", "date_of_birth"),
                  ("Email", "email"), ("Phone", "phone"), ("Address", "address"),
                  ("Occupation", "occupation"), ("Employment status", "employment_status"),
                  ("Monthly income", "monthly_income"), ("Tax residency", "tax_residency")]
        ctx["review_fields"] = [(l, app.data.get(k, "")) for l, k in labels]
        return render(request, "site/wizard_review.html", ctx)
    return render(request, "site/wizard_step.html", ctx)


def wizard_next(request, step: int):
    app = _current_app(request)
    if app is None or request.method != "POST":
        return redirect("portal_open_account")
    idx = min(max(step - 1, 0), TOTAL_STEPS - 1)
    form = {k: v.strip() for k, v in request.POST.items() if k not in ("csrfmiddlewaretoken",)}
    products = request.POST.getlist("products") if idx == 7 else None
    err = save_step(app, idx, form, products=products)
    if err:
        request.session["wizard_error"] = err.replace("_", " ").title()
        return redirect("portal_wizard_step", step=idx + 1)
    return redirect("portal_wizard_step", step=min(idx + 2, TOTAL_STEPS))


def save_and_continue_later(request):
    app = _current_app(request)
    if app is None:
        return redirect("portal_open_account")
    return render(request, "site/save_later.html",
                  {"app": app, "resume_url": f"/application/resume/?token={app.resume_token}"})


def application_resume(request):
    """Resume a draft with reference + email, or a secret token link."""
    if request.method == "POST":
        app = get_or_resume(reference=request.POST.get("reference", ""),
                            email=request.POST.get("email", ""))
        if app is None or app.status != ApplicationStatus.DRAFT:
            return render(request, "site/resume.html", {"error": "No matching draft application."},
                          status=404)
        request.session["portal_application"] = app.reference
        return redirect("portal_wizard_step", step=app.current_step)
    token = request.GET.get("token", "")
    app = get_or_resume(token=token) if token else None
    if app is None:
        return render(request, "site/resume.html")
    request.session["portal_application"] = app.reference
    return redirect("portal_wizard_step", step=app.current_step)


def submit_view(request):
    app = _current_app(request)
    if app is None or request.method != "POST":
        return redirect("portal_open_account")
    try:
        submit_application(app, idempotency_key=request.POST.get("idempotency_key", ""))
    except ApplicationError as e:
        messages.error(request, e.code.replace("_", " ").title())
        return redirect("portal_wizard_step", step=TOTAL_STEPS)
    request.session.pop("portal_application", None)
    audit(action="PORTAL_APPLICATION_SUBMITTED_HTTP")
    return redirect("portal_application_status", reference=app.reference)


def application_status(request, reference: str):
    from .models import AccountApplication

    app = AccountApplication.objects.filter(reference=reference).first()
    if app is None:
        raise Http404
    return render(request, "site/application_status.html",
                  {"app": app, "statuses": ApplicationStatus})


# ------------------------------------------------------------ manager access

def manager_login(request):
    """Dedicated institutional authentication. Only MANAGER role may proceed."""
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = User.objects.filter(username=username).first()
        try:
            auth_user, needs_otp = attempt_login(username, password, request)
        except LoginLocked as e:
            error = str(e)
        else:
            if auth_user is None:
                error = "Invalid credentials"
            elif needs_otp:
                # MFA required before any role decision is made
                request.session["manager_pending_otp_user"] = auth_user.pk
                return redirect("manager_login_otp")
            elif not auth_user.has_role("MANAGER"):
                audit(actor=auth_user, action="MANAGER_LOGIN_DENIED", request=request)
                logout(request)  # never leave a non-manager authenticated via this flow
                error = "Manager role required."
            else:
                login(request, auth_user)
                audit(actor=auth_user, action="MANAGER_LOGIN", request=request)
                return redirect("manager_dashboard")
    elif request.user.is_authenticated and request.user.has_role("MANAGER"):
        return redirect("manager_dashboard")
    return render(request, "site/manager_login.html", {"error": error}, status=403 if error == "Manager role required." else 200)


def manager_login_otp(request):
    uid = request.session.get("manager_pending_otp_user")
    if not uid:
        return redirect("manager_login")
    user = User.objects.get(pk=uid)
    from apps.identity.services import verify_otp
    code = request.POST.get("code", "")
    if request.method == "POST" and verify_otp(user, code):
        del request.session["manager_pending_otp_user"]
        if not user.has_role("MANAGER"):
            audit(actor=user, action="MANAGER_LOGIN_DENIED", request=request)
            return render(request, "site/manager_login.html",
                          {"error": "Manager role required."}, status=403)
        login(request, user)
        audit(actor=user, action="MANAGER_LOGIN_MFA", request=request)
        return redirect("manager_dashboard")
    return render(request, "site/manager_otp.html",
                  {"error": "Invalid code" if request.method == "POST" else ""})


# ------------------------------------------------- manager applications queue

def _require_manager(request):
    if not request.user.is_authenticated or not request.user.has_role("MANAGER"):
        raise PermissionDenied("Manager role required")
    return request.user


def manage_applications(request):
    _require_manager(request)
    from .models import AccountApplication

    reqs = ApprovalRequest.objects.filter(
        operation_type="ONBOARDING_REVIEW").order_by("-created_at")
    apps = {a.pk: a for a in AccountApplication.objects.all()}
    pending = [(r, apps.get(int(r.resource_id))) for r in reqs]
    decided = AccountApplication.objects.exclude(
        status__in=[ApplicationStatus.DRAFT, ApplicationStatus.SUBMITTED,
                    ApplicationStatus.IDENTITY_REVIEW])[:20]
    return render(request, "site/manage_applications.html",
                  {"pending": pending, "decided": decided})


def decide_application_view(request):
    _require_manager(request)
    rid = request.POST.get("approval_id")
    decision = request.POST.get("decision")
    reason = request.POST.get("reason", "")
    from apps.managerops.services import ApprovalError, decide_approval

    try:
        decide_approval(approver=request.user, approval_id=rid,
                        approve=decision == "approve", rejection_reason=reason)
    except (ApprovalError, PermissionDenied, ValueError) as e:
        messages.error(request, getattr(e, "code", str(e)))
    return redirect("portal_manage_applications")