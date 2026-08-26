"""Customer self-service app pages: analytics, accounts, transactions,
investments, cards, security, settings.

All views are login_required and scoped to request.user — a customer only
ever sees their own data.
"""
import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import models
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

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
    if "replay-tour" in request.GET:
        from . import tour as tour_mod
        tour_mod.request_replay(request)
        return redirect("dashboard")
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

    # ---- first-access tutorial: server decides whether it runs (FASE 9)
    from . import tour as tour_mod
    show_tour, _progress = tour_mod.tour_state(u, request)
    if show_tour:
        ctx["tour_steps"] = json.dumps(tour_mod.customer_steps())
        ctx["tour_version"] = tour_mod.TOUR_VERSION
        tour_mod.consume_replay(request)   # one-shot replay flag
    ctx["show_tour"] = show_tour

    return render(request, "dashboard/index.html", ctx)


@login_required
@require_POST
def tour_finish_view(request, outcome):
    """POST-only + CSRF. Records completion or skip; server stays the
    authority on whether the tutorial auto-starts again."""
    from .models import TourProgress
    from . import tour as tour_mod

    if outcome not in ("complete", "skip"):
        raise Http404
    if outcome == "complete":
        tour_mod.mark_completed(request.user)
    else:
        tour_mod.mark_skipped(request.user)
    assert TourProgress.objects.filter(user=request.user).exists()
    if request.headers.get("HX-Request") or request.headers.get("Accept", "").startswith("application/json") \
            or request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": True, "outcome": outcome})
    return redirect("dashboard")


@login_required
@customer_only
def tour_replay_view(request):
    """Ajuda → 'Ver tutorial novamente' (server-side one-shot flag)."""
    from . import tour as tour_mod
    tour_mod.request_replay(request)
    return redirect("dashboard")


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
def card_detail_view(request, card_id):
    """FASE 8 B1: per-card detail. Server-side ownership; IDOR -> 404."""
    from django.http import Http404
    from apps.cards.models import Card
    from apps.cards.services import credit_availability, outstanding_statement_total

    card = Card.objects.select_related("account", "account__customer").filter(
        pk=card_id, account__customer=request.user).first()
    if card is None:
        raise Http404("Card not found")
    used, available = credit_availability(card)
    txs = card.transactions.all()[:10]
    return render(request, "dashboard/card_detail.html", {
        "nav": "cards", "page_heading": f"Card •••• {card.last4}",
        "card": card,
        "credit_used": used,
        "credit_available": available,
        "outstanding_total": outstanding_statement_total(card),
        "recent_transactions": txs,
    })


@login_required
@customer_only
def card_control_view(request, card_id):
    """FASE 8 B2: POST-only customer controls (freeze/unfreeze/online/
    international/lost). Ownership server-side; audited by domain services."""
    from django.core.exceptions import PermissionDenied
    from django.http import Http404
    from django.shortcuts import redirect
    from apps.cards.models import Card, CardStatus
    from apps.cards.services import (CardDeclined, freeze_card,
                                     report_lost_or_stolen, set_card_control,
                                     unfreeze_card)

    if request.method != "POST":
        return redirect("app_card_detail", card_id)
    card = Card.objects.filter(pk=card_id, account__customer=request.user).first()
    if card is None:
        raise Http404("Card not found")
    action = request.POST.get("action", "")
    try:
        if action == "freeze":
            freeze_card(request.user, card_id)
            messages.success(request, "Card frozen.")
        elif action == "unfreeze":
            unfreeze_card(request.user, card_id)
            messages.success(request, "Card unfrozen.")
        elif action == "toggle_online":
            set_card_control(request.user, card_id,
                             online_enabled=not card.online_enabled)
            messages.success(request, "Online purchases updated.")
        elif action == "toggle_international":
            set_card_control(request.user, card_id,
                             international_enabled=not card.international_enabled)
            messages.success(request, "International purchases updated.")
        elif action == "report_lost":
            report_lost_or_stolen(request.user, card_id)
            messages.success(request, "Card blocked and reported lost.")
        else:
            messages.error(request, "Unknown control action.")
    except (CardDeclined, PermissionDenied) as e:
        messages.error(request, getattr(e, "reason", None) or str(e))
    return redirect("app_card_detail", card_id)


@login_required
@customer_only
def card_transactions_view(request, card_id):
    """FASE 8 B4: card transaction history. Server-side filters + pagination."""
    from datetime import datetime

    from django.core.paginator import Paginator
    from django.http import Http404
    from django.utils import timezone
    from apps.cards.models import Card, CardTransaction

    card = Card.objects.filter(pk=card_id, account__customer=request.user).first()
    if card is None:
        raise Http404("Card not found")
    qs = card.transactions.all()
    status = request.GET.get("status", "")
    if status == "approved":
        qs = qs.filter(declined=False)
    elif status == "declined":
        qs = qs.filter(declined=True)
    period_from = period_to = ""
    try:
        raw = request.GET.get("from", "")
        period_from = raw
        if raw:
            qs = qs.filter(created_at__date__gte=datetime.strptime(raw, "%Y-%m-%d").date())
        raw = request.GET.get("to", "")
        period_to = raw
        if raw:
            qs = qs.filter(created_at__date__lte=datetime.strptime(raw, "%Y-%m-%d").date())
    except ValueError:
        pass
    merchant = request.GET.get("merchant", "")[:80]
    if merchant:
        qs = qs.filter(merchant__icontains=merchant)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "dashboard/card_transactions.html", {
        "nav": "cards", "page_heading": f"Transactions •••• {card.last4}",
        "card": card, "page": page,
        "status": status if status in ("", "approved", "declined") else "",
        "period_from": period_from, "period_to": period_to,
        "merchant": merchant,
    })


@login_required
@customer_only
def card_transaction_detail_view(request, card_id, tx_id):
    """FASE 8 B4: ownership validated across user + card + transaction."""
    from django.http import Http404
    from apps.cards.models import Card, CardTransaction

    tx = CardTransaction.objects.select_related(
        "card__account__customer", "journal").filter(
        pk=tx_id, card_id=card_id,
        card__account__customer=request.user).first()
    if tx is None:
        raise Http404("Transaction not found")
    return render(request, "dashboard/card_transaction_detail.html", {
        "nav": "cards",
        "page_heading": f"Transaction •••• {tx.card.last4}",
        "card": tx.card, "tx": tx,
    })


@login_required
@customer_only
def card_invoices_view(request, card_id):
    """FASE 8 B6: current open invoice + paginated previous statements."""
    from django.core.paginator import Paginator
    from django.http import Http404
    from django.utils import timezone
    from apps.cards.billing import open_cycle_composition
    from apps.cards.models import Card, CreditStatement

    card = Card.objects.filter(pk=card_id, account__customer=request.user).first()
    if card is None:
        raise Http404("Card not found")
    today = timezone.now().date()
    open_lines = open_cycle_composition(card)[:25]
    open_total = sum(t.amount for t in open_cycle_composition(card))
    stmts = Paginator(card.statements.order_by("-period_end"), 12).get_page(
        request.GET.get("page"))
    return render(request, "dashboard/card_invoices.html", {
        "nav": "cards", "page_heading": f"Invoices •••• {card.last4}",
        "card": card,
        "open_lines": open_lines,
        "open_total": open_total,
        "next_close": today.replace(day=1),
        "stmts_page": stmts,
    })


@login_required
@customer_only
def card_invoice_detail_view(request, card_id, statement_id):
    """FASE 8 B6: closed invoice detail. Ownership user+card+statement."""
    from django.http import Http404
    from apps.cards.billing import statement_composition
    from apps.cards.models import Card, CreditStatement

    stmt = CreditStatement.objects.select_related(
        "card__account__customer").filter(
        pk=statement_id, card_id=card_id,
        card__account__customer=request.user).first()
    if stmt is None:
        raise Http404("Invoice not found")
    lines = list(statement_composition(stmt))
    derived = sum(t.amount for t in lines)
    return render(request, "dashboard/card_invoice_detail.html", {
        "nav": "cards",
        "page_heading": f"Invoice •••• {stmt.card.last4}",
        "card": stmt.card, "stmt": stmt, "lines": lines,
        "derived_total": derived,
        "consistent": derived == stmt.amount_due,
    })


@login_required
@customer_only
def card_pay_invoice_view(request, card_id):
    """FASE 8 B7: POST-only invoice payment from the linked account."""
    from django.core.exceptions import PermissionDenied
    from django.http import Http404
    from django.shortcuts import redirect
    from apps.cards.models import Card
    from apps.cards.services import CardDeclined, pay_statement

    if request.method != "POST":
        return redirect("app_card_invoices", card_id)
    card = Card.objects.filter(pk=card_id, account__customer=request.user).first()
    if card is None:
        raise Http404("Card not found")
    statement_id = request.POST.get("statement") or None
    try:
        total = pay_statement(actor=request.user, card_id=card.pk,
                              statement_id=statement_id,
                              idempotency_key=f"ui:{card.pk}:{request.user.pk}:{statement_id or 'all'}")
        messages.success(request, f"Invoice payment of ${total} completed.")
    except (CardDeclined, PermissionDenied) as e:
        messages.error(request, getattr(e, "reason", None) or str(e))
    return redirect("app_card_invoices", card_id)


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
    risk_confirm_open = False
    form = ChangePasswordForm(user=u)   # rendered on GET; rebind on POST below
    if request.method == "POST" and "change_password" in request.POST:
        form = ChangePasswordForm(user=u, data=request.POST)
        if form.is_valid():
            # Risk runs BEFORE the password is touched: a challenge failure or
            # a block must leave the old password intact. Under enforcement an
            # engine error propagates out of evaluate_profile_change (no
            # except-pass) and lands here as fail-closed BLOCK.
            from apps.fraud.profile_risk import evaluate_profile_change
            from .services import sensitive_action_decision

            evaluation = None
            try:
                evaluation = evaluate_profile_change(
                    u, request=request, operation_type="PASSWORD_CHANGE")
                action = sensitive_action_decision(evaluation)
            except Exception:
                action = "BLOCK"

            if action == "CHALLENGE":
                code = request.POST.get("risk_code", "")
                if code:
                    from .services import verify_otp

                    if verify_otp(u, code):
                        action = "ALLOW"      # second factor satisfied
                    else:
                        pw_error = "Invalid or expired verification code."
                        action = None         # password NOT changed
                else:
                    from .services import _deliver_otp

                    _deliver_otp(u, "password change")
                    risk_confirm_open = True
                    action = None             # awaiting the code; not applied yet
            elif action == "BLOCK":
                from apps.audit.services import record as audit_record

                audit_record(actor=u, action="PASSWORD_CHANGE_BLOCKED",
                             request=request,
                             metadata={"evaluation": getattr(evaluation, "pk", None)})
                messages.error(request, "Password change was not applied.")
                return redirect("app_security")

            if action == "ALLOW":
                user = form.save()
                update_session_auth_hash(request, user)
                audit(actor=u, action="PASSWORD_CHANGED", request=request)
                from apps.notifications.services import notify

                notify(recipient=u, category="SECURITY", kind="PASSWORD_CHANGED",
                       title="Password changed",
                       body="Your password was changed. If this was not you, "
                            "contact support immediately.",
                       dedup_key=f"PASSWORD_CHANGED:{u.pk}:{uuid.uuid4().hex}")
                messages.success(request, "Password changed successfully.")
                return redirect("app_security")
        else:
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
        elif "mfa_enable_start" in request.POST or "mfa_enable_confirm" in request.POST \
                or "mfa_disable" in request.POST:
            from .services import MFAError, confirm_mfa_enable, disable_mfa, start_mfa_enable

            try:
                if "mfa_enable_start" in request.POST:
                    start_mfa_enable(u, request=request)
                    messages.success(request,
                                     "A confirmation code was sent to you. Enter it below to enable MFA.")
                elif "mfa_enable_confirm" in request.POST:
                    confirm_mfa_enable(u, request.POST.get("mfa_code") or "", request=request)
                    messages.success(request, "MFA enabled.")
                else:
                    disable_mfa(u, request.POST.get("password") or "", request=request)
                    messages.success(request, "MFA disabled.")
                    return redirect("app_security")
            except MFAError as e:
                messages.error(request, {
                    "INVALID_OR_EXPIRED_CODE": "Invalid or expired code.",
                    "REAUTHENTICATION_REQUIRED": "Password incorrect — MFA is still enabled.",
                }.get(e.code, str(e)))
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
        "form": form,
        "devices": devices,
        "sessions": sessions,
        "history_page": page,
        "history_entries": history_entries,
        "mfa_enabled": u.mfa_enabled,
        "mfa_confirm_open": request.POST.get("mfa_enable_start") == "1",
        "risk_confirm_open": risk_confirm_open,
    })


@login_required
@customer_only
def settings_view(request):
    u = request.user
    return render(request, "dashboard/settings.html", {
        "nav": "settings", "page_heading": "Settings", "profile": u,
    })


@login_required
@customer_only
def account_statement_view(request, account_id):
    """Per-account statement (FASE 5). Read-only projection of the ledger;
    ownership enforced server-side — foreign accounts are indistinguishable
    from nonexistent ones."""
    from django.core.paginator import Paginator
    from django.http import Http404
    from apps.accounts.statement import (
        apply_filters, get_owned_account, statement_lines, statement_queryset,
    )

    try:
        account = get_owned_account(request.user, account_id)
    except Account.DoesNotExist:
        raise Http404("Account not found")

    filtered, active_filters = apply_filters(statement_queryset(account), account, request.GET)
    entries = Paginator(filtered, 25).get_page(request.GET.get("page"))
    lines = statement_lines(account, entries)
    return render(request, "dashboard/statement.html", {
        "nav": "accounts", "page_heading": "Statement",
        "account": account,
        "balance": account.current_balance,
        "masked_number": f"•••• {account.account_number[-4:]}",
        "page": entries,
        "lines": lines,
        "filters": active_filters,
        "period_choices": [("today", "Today"), ("7d", "Last 7 days"),
                           ("30d", "Last 30 days"), ("month", "This month"),
                           ("custom", "Custom")],
        "source_choices": [("TRANSFER", "Transfers"), ("PAYMENT", "Payments"),
                           ("CARD", "Cards"), ("OTHER", "Other")],
    })


def _owned_journal_or_404(user, reference):
    """Resolve a journal by its public reference and enforce ownership."""
    from django.http import Http404
    from apps.accounts.models import Account as _A
    from apps.ledger.models import JournalEntry

    journal = JournalEntry.objects.filter(reference=reference).first()
    if journal is None or journal.status != "POSTED":
        # drafts are uncommitted financial facts and must never be visible
        raise Http404("Transaction not found")
    owned = _A.objects.filter(
        customer=user, ledger_account__entries__journal=journal
    ).exists()
    if not owned:
        # foreign or nonexistent — indistinguishable responses
        raise Http404("Transaction not found")
    return journal


def _operation_for(journal):
    from apps.cards.models import CardTransaction
    from apps.payments.models import Payment
    from apps.transfers.models import Transfer

    t = Transfer.objects.filter(journal=journal).select_related(
        "source_account", "destination_account", "beneficiary").first()
    if t:
        return "TRANSFER", t
    p = Payment.objects.filter(journal=journal).select_related("bill").first()
    if p:
        return "PAYMENT", p
    c = CardTransaction.objects.filter(journal=journal).first()
    if c:
        return "CARD", c
    return "JOURNAL", None


@login_required
@customer_only
def transaction_detail_view(request, reference):
    from django.http import Http404

    journal = _owned_journal_or_404(request.user, reference)
    op_type, op = _operation_for(journal)
    reversal = journal.reversed_by.first()
    original = journal.reverses
    ctx = {
        "nav": "transactions", "page_heading": "Transaction",
        "journal": journal, "op_type": op_type, "op": op,
        "reversal_reference": reversal.reference if reversal else None,
        "original_reference": original.reference if original else None,
        "receipt_available": op is not None and getattr(op, "status", None) in ("COMPLETED", "REVERSED"),
    }
    if op_type == "TRANSFER":
        ctx["counterparty"] = (
            op.destination_account.account_number[-4:] if op.destination_account_id
            else (op.beneficiary.name if op.beneficiary_id else "External"))
        ctx["direction"] = "OUT" if op.source_account.customer_id == request.user.id else "IN"
    elif op_type == "PAYMENT":
        ctx["counterparty"] = op.bill.biller
        ctx["direction"] = "OUT"
    elif op_type == "CARD":
        ctx["counterparty"] = op.merchant
        ctx["direction"] = "OUT"
    return render(request, "dashboard/transaction_detail.html", ctx)


@login_required
@customer_only
def transaction_receipt_view(request, reference):
    """Read-only receipt; never persists anything, never posts to the ledger.
    Only effectively-completed operations carry a receipt."""
    from django.http import Http404

    journal = _owned_journal_or_404(request.user, reference)
    op_type, op = _operation_for(journal)
    reversible = {"COMPLETED", "REVERSED"}
    ok = (op is not None
          and getattr(op, "status", None) in reversible
          and journal.status == "POSTED"
          and not (op_type == "CARD" and op.declined))
    if not ok:
        raise Http404("Receipt not available")
    entry = journal.entries.select_related("account__bank_account").filter(
        account__is_customer_account=True).order_by("-amount").first()
    amount = entry.amount if entry else None
    direction = ("OUT" if entry and entry.side == "DEBIT" else "IN") if entry else ""
    reversal = journal.reversed_by.first()
    return render(request, "dashboard/receipt.html", {
        "journal": journal, "op_type": op_type, "op": op,
        "amount": amount, "direction": direction,
        "status": getattr(op, "status", journal.status),
        "reversed": reversal is not None,
        "reversal_reference": reversal.reference if reversal else None,
    })


def _csv_safe(value):
    """Mitigate CSV/spreadsheet formula injection for user-controllable text."""
    import csv as _csv

    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{value}"
    return value


@login_required
@customer_only
def account_statement_export(request, account_id):
    """CSV export — strictly the StatementService + same GET filters.
    Streaming with a hard row cap; never a second independent query path."""
    import csv
    from django.http import StreamingHttpResponse
    from django.core.paginator import Paginator
    from django.http import Http404
    from apps.accounts.statement import apply_filters, get_owned_account, statement_lines, statement_queryset
    from apps.audit.services import record as audit

    try:
        account = get_owned_account(request.user, account_id)
    except Account.DoesNotExist:
        raise Http404("Account not found")

    MAX_ROWS = 5000
    filtered, active_filters = apply_filters(statement_queryset(account), account, request.GET)
    total = Paginator(filtered, 1000).count
    rows = min(total, MAX_ROWS)

    def generate():
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Date", "Description", "Type", "In", "Out", "Balance", "Reference"])
        yield buf.getvalue()
        buf.seek(0); buf.truncate()
        emitted = 0
        for chunk_start in range(0, rows, 500):
            page = filtered[chunk_start:chunk_start + 500]
            for line in statement_lines(account, page):
                w.writerow([
                    line.timestamp.strftime("%Y-%m-%d %H:%M"),
                    _csv_safe(line.description),
                    line.operation_type,
                    str(line.amount) if line.direction == "IN" else "",
                    str(line.amount) if line.direction == "OUT" else "",
                    str(line.balance_after),
                    _csv_safe(line.operation_reference),
                ])
            yield buf.getvalue()
            buf.seek(0); buf.truncate()
            emitted += len(page)

    response = StreamingHttpResponse(generate(), content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="statement-{account.account_number}.csv"')
    audit(actor=request.user, action="STATEMENT_EXPORTED",
          metadata={"account": account.account_number[-4:], "rows": rows})
    return response


@login_required
@customer_only
def account_statement_print(request, account_id):
    """Print-friendly HTML statement (no PDF engine added). Same service+filters."""
    from django.core.paginator import Paginator
    from django.http import Http404
    from apps.accounts.statement import apply_filters, get_owned_account, statement_lines, statement_queryset

    try:
        account = get_owned_account(request.user, account_id)
    except Account.DoesNotExist:
        raise Http404("Account not found")

    PRINT_MAX = 300
    filtered, active_filters = apply_filters(statement_queryset(account), account, request.GET)
    entries = Paginator(filtered, PRINT_MAX).get_page(1)
    lines = statement_lines(account, entries)[:PRINT_MAX]
    return render(request, "dashboard/statement_print.html", {
        "account": account, "balance": account.current_balance,
        "masked_number": f"•••• {account.account_number[-4:]}",
        "lines": lines, "truncated": filtered.count() > PRINT_MAX,
    })


@login_required
@customer_only
def notifications_view(request):
    """Central de Notificações (FASE 6). Read-model customer-facing."""
    from django.core.paginator import Paginator
    from apps.notifications.models import Notification, NotificationPreference
    from apps.notifications.models import Category

    if request.method == "POST" and "pref_category" in request.POST:
        from apps.notifications.services import set_category_preference
        cat = request.POST.get("pref_category", "")
        if cat in Category.values:
            set_category_preference(actor=request.user, category=cat,
                                    enabled=request.POST.get("pref_enabled") == "1")
        return redirect("app_notifications")

    qs = Notification.objects.filter(recipient=request.user)
    state = request.GET.get("state", "")
    if state == "unread":
        qs = qs.filter(read=False)
    elif state == "read":
        qs = qs.filter(read=True)
    category = request.GET.get("category", "")
    if category in Category.values:
        qs = qs.filter(category=category)
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    pref_map = dict(NotificationPreference.objects.filter(
        user=request.user).values_list("category", "enabled"))
    pref_rows = [
        {"value": value, "label": label, "enabled": pref_map.get(value, True)}
        for value, label in Category.choices
    ]
    return render(request, "dashboard/notifications.html", {
        "nav": "notifications", "page_heading": "Notifications",
        "page": page,
        "state": state if state in ("", "unread", "read") else "",
        "category": category,
        "categories": Category.choices,
        "pref_rows": pref_rows,
    })


@login_required
@customer_only
def notification_read_view(request, notification_id):
    """POST-only mark-read; ownership enforced; idempotent."""
    from django.http import Http404
    from django.shortcuts import redirect
    from apps.notifications.services import mark_read

    from apps.notifications.models import Notification as _Notification

    if request.method != "POST":
        return redirect("app_notifications")
    note = _Notification.objects.filter(
        recipient=request.user, pk=notification_id).first()
    if note is None:
        raise Http404("Notification not found")
    mark_read(note)
    return redirect("app_notifications")


@login_required
@customer_only
def notifications_mark_all_read_view(request):
    from django.shortcuts import redirect
    from apps.notifications.services import mark_all_read

    if request.method == "POST":
        mark_all_read(request.user)
    return redirect("app_notifications")
