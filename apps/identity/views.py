from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from apps.audit.services import record as audit
from .forms import LoginForm, OTPForm
from .services import attempt_login, verify_otp, verify_totp, LoginLocked, LoginRiskBlocked


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            try:
                user, needs_otp = attempt_login(form.cleaned_data["username"], form.cleaned_data["password"], request)
            except LoginLocked as e:
                form.add_error(None, str(e))
                return render(request, "auth/login.html", {"form": form})
            except LoginRiskBlocked:
                # generic on purpose: no risk internals leak to the client
                form.add_error(None, "Unable to sign in with these credentials.")
                return render(request, "auth/login.html", {"form": form})
            if user is None:
                form.add_error(None, "Invalid credentials")
                return render(request, "auth/login.html", {"form": form})
            request.session["pending_otp_user"] = user.pk
            if needs_otp:
                return redirect("otp_verify")
            login(request, user)
            from .services import bind_session

            bind_session(request, user)
            audit(actor=user, action="LOGIN", request=request)
            return redirect("dashboard")
    else:
        form = LoginForm()
    return render(request, "auth/login.html", {"form": form})


def otp_verify_view(request):
    uid = request.session.get("pending_otp_user")
    if not uid:
        return redirect("login")
    from .models import User

    user = User.objects.get(pk=uid)
    if request.method == "POST":
        form = OTPForm(request.POST)
        code_ok = False
        if form.is_valid():
            code = form.cleaned_data["code"]
            code_ok = verify_otp(user, code) or verify_totp(user, code)
        if not code_ok:
            # brute-force guard: too many bad codes force a full re-login
            attempts = request.session.get("otp_attempts", 0) + 1
            if attempts >= 5:
                del request.session["pending_otp_user"]
                request.session.pop("otp_attempts", None)
                form.add_error(None, "Too many attempts — sign in again.")
                return render(request, "auth/otp.html", {"form": form})
            request.session["otp_attempts"] = attempts
        if code_ok:
            login(request, user)
            from .services import bind_session

            bind_session(request, user)
            del request.session["pending_otp_user"]
            request.session.pop("otp_attempts", None)
            audit(actor=user, action="LOGIN_MFA", request=request)
            return redirect("dashboard")
        # real producer for the MFA_FAILURE_COUNT_24H auth signal
        audit(actor=user if form.is_valid() else None,
              action="LOGIN_MFA", request=request,
              metadata={"otp_failure": True})
        form.add_error(None, "Invalid code")
    else:
        form = OTPForm()
    return render(request, "auth/otp.html", {"form": form})


@login_required
def logout_view(request):
    audit(actor=request.user, action="LOGOUT", request=request)
    logout(request)
    return redirect("login")


@login_required
def dashboard_view(request):
    """Role-aware landing: customers get the fintech dashboard; staff get their portal."""
    u = request.user
    if u.is_customer:
        from . import app_views

        return app_views.dashboard(request)

    role = u.role
    if role == "MANAGER":
        return redirect("manager_dashboard")
    portal_map = {
        "CARD_OPS_ANALYST": "portal/card_ops.html",
        "COMPLIANCE_ANALYST": "portal/compliance.html",
        "SUPPORT_AGENT": "portal/support.html",
        "ADMIN": "portal/admin.html",
        "AUDITOR": "portal/auditor.html",
    }
    template = portal_map.get(role)
    if not template:
        return HttpResponseForbidden("Unknown role")
    return render(request, template, {})
