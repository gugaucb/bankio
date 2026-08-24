"""Customer self-service app pages: analytics, accounts, transactions,
investments, cards, security, settings.

All views are login_required and scoped to request.user — a customer only
ever sees their own data.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import models
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.accounts.models import Account
from apps.audit.services import record as audit
from apps.cards.models import Card, CardRequest, CardTransaction
from apps.cards.services import CardRequestError, request_card
from apps.investments.models import Position
from apps.transfers.models import Transfer

from .forms import ChangePasswordForm


def customer_only(view):
    """Restrict a view to customers; staff are redirected to their own portal."""
    import functools

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_customer:
            return redirect("manager_dashboard")
        return view(request, *args, **kwargs)
    return wrapper


def _customer_accounts(u):
    return Account.objects.filter(customer=u).select_related("ledger_account")


def _completed_transfers(u, since=None):
    qs = Transfer.objects.filter(
        status="COMPLETED",
    ).filter(source_account__customer=u) | Transfer.objects.filter(
        status="COMPLETED", destination_account__customer=u,
    )
    if since:
        qs = qs.filter(created_at__gte=since)
    return qs


def _money_metrics(u):
    now = timezone.now()
    month_start = now - timedelta(days=30)
    income = Decimal("0")
    expenses = Decimal("0")
    for t in Transfer.objects.filter(status="COMPLETED", created_at__gte=month_start,
                                     destination_account__customer=u):
        income += t.amount
    for t in Transfer.objects.filter(status="COMPLETED", created_at__gte=month_start,
                                     source_account__customer=u):
        expenses += t.amount
    balances = sum((a.current_balance for a in _customer_accounts(u)), Decimal("0"))
    positions_value = sum((p.market_value for p in Position.objects.filter(customer=u)), Decimal("0"))
    return {
        "income": income,
        "expenses": expenses,
        "net_worth": balances + positions_value,
        "balances": balances,
        "positions_value": positions_value,
    }


@login_required
@customer_only
def dashboard(request):
    u = request.user
    if not u.is_customer:
        return redirect("manager_dashboard")
    accounts = _customer_accounts(u)
    primary = accounts.first()
    txs = []
    outgoing = Transfer.objects.select_related("destination_account__customer", "beneficiary").filter(
        source_account__customer=u).order_by("-created_at")[:10]
    incoming = Transfer.objects.select_related("source_account__customer").filter(
        destination_account__customer=u).order_by("-created_at")[:10]
    merged = sorted(list(outgoing) + list(incoming), key=lambda t: t.created_at, reverse=True)[:8]
    for t in merged:
        is_out = t.source_account.customer_id == u.id
        name = (
            t.destination_account.customer.get_full_name() if not is_out and t.destination_account_id
            else (t.beneficiary.name if is_out and t.beneficiary_id else "Transfer")
        )
        txs.append({"name": name or "Transfer", "amount": t.amount,
                    "sign": "-" if is_out else "+", "date": t.created_at})
    positions = Position.objects.filter(customer=u).select_related("instrument")
    metrics = _money_metrics(u)

    # spending by month (last 6 months, completed outflows)
    months, spend_by_month = [], []
    for i in range(5, -1, -1):
        start = (timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30 * i))
        end = start + timedelta(days=31)
        label = start.strftime("%b")
        total = sum((t.amount for t in Transfer.objects.filter(
            status="COMPLETED", source_account__customer=u,
            created_at__gte=start, created_at__lt=end)), Decimal("0"))
        months.append(label)
        spend_by_month.append(float(total))

    # portfolio per instrument
    perf_labels = [p.instrument.symbol for p in positions] or ["No holdings"]
    perf_values = [float(p.market_value) for p in positions] or [0]

    card_spend = sum(t.amount for t in CardTransaction.objects.filter(card__account__customer=u, declined=False))
    cards = Card.objects.filter(account__customer=u)
    ctx = {
        "nav": "dashboard",
        "accounts": accounts,
        "primary": primary,
        "transactions": txs,
        "positions": positions,
        "card_spend": card_spend,
        "cards": cards,
        "metrics": metrics,
        "chart_months": json.dumps(months),
        "chart_spend": json.dumps(spend_by_month),
        "chart_perf_labels": json.dumps(perf_labels),
        "chart_perf_values": json.dumps(perf_values),
    }
    return render(request, "dashboard/index.html", ctx)


@login_required
@customer_only
def analytics(request):
    u = request.user
    m = _money_metrics(u)
    # top destinations last 90 days
    since = timezone.now() - timedelta(days=90)
    by_dest = {}
    for t in Transfer.objects.filter(status="COMPLETED", source_account__customer=u, created_at__gte=since):
        key = t.beneficiary.name if t.beneficiary_id else (
            t.destination_account.customer.get_full_name() if t.destination_account_id else "External")
        by_dest[key or "External"] = by_dest.get(key or "External", Decimal("0")) + t.amount
    top = sorted(by_dest.items(), key=lambda kv: -kv[1])[:6]
    monthly_out = []
    labels = []
    for i in range(5, -1, -1):
        start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30 * i)
        total = sum((t.amount for t in Transfer.objects.filter(
            status="COMPLETED", source_account__customer=u,
            created_at__gte=start, created_at__lt=start + timedelta(days=31))), Decimal("0"))
        labels.append(start.strftime("%b"))
        monthly_out.append(float(total))
    ctx = {
        "nav": "analytics",
        "page_heading": "Analytics",
        "metrics": m,
        "top_destinations": top,
        "chart_labels": json.dumps(labels),
        "chart_data": json.dumps(monthly_out),
    }
    return render(request, "dashboard/analytics.html", ctx)


@login_required
@customer_only
def accounts_view(request):
    u = request.user
    accounts = _customer_accounts(u)
    totals = {a.pk: {"out": Decimal("0"), "in": Decimal("0")} for a in accounts}
    since = timezone.now() - timedelta(days=30)
    for t in Transfer.objects.filter(source_account__customer=u, created_at__gte=since):
        totals[t.source_account_id]["out"] += t.amount
    for t in Transfer.objects.filter(destination_account__customer=u, created_at__gte=since):
        if t.destination_account_id in totals:
            totals[t.destination_account_id]["in"] += t.amount
    rows = [(a, totals[a.pk]["in"], totals[a.pk]["out"]) for a in accounts]
    return render(request, "dashboard/accounts.html", {
        "nav": "accounts", "page_heading": "Accounts", "rows": rows,
        "metrics": _money_metrics(u),
    })


@login_required
@customer_only
def transactions_view(request):
    u = request.user
    flt = request.GET.get("dir", "")
    qs = (Transfer.objects.select_related("source_account__customer",
                                          "destination_account__customer", "beneficiary")
          .filter(source_account__customer=u) | Transfer.objects.select_related(
              "source_account__customer").filter(destination_account__customer=u))
    if flt == "in":
        qs = qs.filter(destination_account__customer=u)
    elif flt == "out":
        qs = qs.filter(source_account__customer=u)
    txs = []
    for t in qs.order_by("-created_at")[:100]:
        is_out = t.source_account.customer_id == u.id
        other = (t.destination_account.customer.get_full_name() if not is_out and t.destination_account_id
                 else (t.beneficiary.name if t.beneficiary_id else "External"))
        txs.append({"obj": t, "direction": "out" if is_out else "in", "other": other or "External"})
    card_txs = CardTransaction.objects.filter(card__account__customer=u).select_related("card")[:50]
    return render(request, "dashboard/transactions.html", {
        "nav": "transactions", "page_heading": "Transactions", "txs": txs,
        "card_txs": card_txs, "filter": flt,
    })


@login_required
@customer_only
def investments_view(request):
    u = request.user
    positions = Position.objects.filter(customer=u).select_related("instrument")
    pos_rows = []
    for p in positions:
        cost = p.quantity * p.avg_price
        pos_rows.append({"p": p, "cost": cost, "pl": p.market_value - cost, "pl_abs": abs(p.market_value - cost),
                         "pl_pct": ((p.market_value - cost) / cost * 100) if cost else Decimal("0")})
    total_cost = sum((p.quantity * p.avg_price for p in positions), Decimal("0"))
    total_value = sum((p.market_value for p in positions), Decimal("0"))
    orders = u.orders.select_related("instrument").order_by("-created_at")[:20]
    pl = total_value - total_cost
    return render(request, "dashboard/investments.html", {
        "nav": "investments", "page_heading": "Investments", "positions": positions,
        "pos_rows": pos_rows,
        "orders": orders, "total_cost": total_cost, "total_value": total_value,
        "pl": pl, "pl_pct": (pl / total_cost * 100) if total_cost else Decimal("0"),
    })


@login_required
@customer_only
def cards_view(request):
    u = request.user
    cards = Card.objects.filter(account__customer=u).select_related("account")
    requests = CardRequest.objects.filter(customer=u).select_related("account")
    my_accounts = _customer_accounts(u).filter(status="ACTIVE")
    error = None
    if request.method == "POST" and "request_card" in request.POST:
        try:
            req = request_card(
                u,
                account_id=request.POST.get("account"),
                card_type=request.POST.get("type", "CREDIT_CARD"),
                requested_limit=request.POST.get("limit"),
            )
            messages.success(request, f"Card request #{req.pk} submitted for approval.")
            return redirect("app_cards")
        except (CardRequestError, Account.DoesNotExist) as e:
            error = getattr(e, "code", "INVALID_REQUEST").replace("_", " ").title()
    return render(request, "dashboard/cards.html", {
        "nav": "cards", "page_heading": "Cards", "cards": cards,
        "requests": requests, "my_accounts": my_accounts, "error": error,
    })


@login_required
@customer_only
def security_view(request):
    u = request.user
    pw_error = None
    if request.method == "POST" and "change_password" in request.POST:
        form = ChangePasswordForm(user=u, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            audit(actor=u, action="PASSWORD_CHANGED", request=request)
            # shadow risk observation on sensitive profile change (spec PART 27)
            from apps.fraud.profile_risk import evaluate_profile_change

            try:
                evaluate_profile_change(u, request=request, operation_type="PASSWORD_CHANGE")
            except Exception:
                pass  # never fatal; evaluate_profile_change already audits errors
            messages.success(request, "Password changed successfully.")
            return redirect("app_security")
        pw_error = "; ".join(" ".join(v) for v in form.errors.values())

    elif request.method == "POST":
        if "revoke_session" in request.POST or "revoke_other_sessions" in request.POST:
            from .services import SessionError, revoke_other_sessions

            try:
                if "revoke_session" in request.POST:
                    revoke_other_sessions(u, request,
                                          session_key=request.POST.get("session") or None)
                    messages.success(request, "Session signed out.")
                else:
                    n = revoke_other_sessions(u, request)
                    messages.success(request, f"{n} other session(s) signed out.")
            except SessionError:
                pass
        else:
            from .services import DeviceError, revoke_device, trust_device, untrust_device

            try:
                if "trust_device" in request.POST:
                    trust_device(u, request.POST.get("device"), request=request)
                    messages.success(request, "Device marked as trusted.")
                elif "untrust_device" in request.POST:
                    untrust_device(u, request.POST.get("device"), request=request)
                    messages.success(request, "Device trust revoked.")
                elif "revoke_device" in request.POST:
                    revoke_device(u, request.POST.get("device"), request=request)
                    messages.success(request, "Device removed.")
            except (DeviceError, ValueError, TypeError):
                # foreign/unknown ids are silent no-ops; back to the page
                pass
        return redirect("app_security")

    from apps.audit.models import AuditLog

    events = AuditLog.objects.filter(actor=u, action__startswith="LOGIN").order_by("-timestamp")[:10]
    recent_logins = [(e.action, e.timestamp, e.ip_address) for e in events]

    # ---- security activity history (server-side pagination, safe fields only)
    from django.core.paginator import Paginator

    _safe_actions = [
        "LOGIN", "LOGIN_FAILED", "LOGIN_MFA", "LOGOUT", "PASSWORD_CHANGED",
        "DEVICE_TRUSTED", "DEVICE_UNTRUSTED", "DEVICE_REVOKED",
        "SESSION_REVOKED", "OTHER_SESSIONS_REVOKED",
    ]
    history_qs = AuditLog.objects.filter(actor=u).filter(
        models.Q(action__in=_safe_actions) | models.Q(action__startswith="CHALLENGE")
    ).order_by("-timestamp")
    paginator = Paginator(history_qs, 10)
    page = paginator.get_page(request.GET.get("page"))
    history_entries = [{"action": e.action,
                        "label": e.action.replace("_", " ").title(),
                        "failed": "FAILED" in e.action,
                        "timestamp": e.timestamp} for e in page]

    from django.contrib.sessions.models import Session

    from .services import current_device_hash

    current_hash = current_device_hash(request)
    devices = []
    for d in u.devices.order_by("-last_seen"):
        devices.append({"obj": d, "current": d.device_id == current_hash})

    # live sessions of this user only; stale records pruned on sight
    live_keys = set(Session.objects.filter(
        session_key__in=u.session_records.values_list("session_key", flat=True))
        .values_list("session_key", flat=True))
    current_key = request.session.session_key
    u.session_records.exclude(session_key__in=live_keys).delete()
    sessions = []
    for s in u.session_records.order_by("-created_at"):
        sessions.append({"obj": s, "current": s.session_key == current_key})
    return render(request, "dashboard/security.html", {
        "nav": "security", "page_heading": "Security",
        "recent_logins": recent_logins, "pw_error": pw_error,
        "devices": devices,
        "sessions": sessions,
        "history_page": page,
        "history_entries": history_entries,
    })


@login_required
@customer_only
def settings_view(request):
    u = request.user
    return render(request, "dashboard/settings.html", {
        "nav": "settings", "page_heading": "Settings", "profile": u,
    })
