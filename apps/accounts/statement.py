"""Statement projection service (FASE 5).

Read-model only: transforms POSTED ledger activity into customer-facing
statement lines. No persistence, no parallel history table — the ledger
remains the single source of truth.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Case, F, Sum, Value, When, Window
from django.db.models import DecimalField as models_DecimalField
from django.db.models.functions import Coalesce

from apps.ledger.models import JournalEntry, LedgerEntry


@dataclass(frozen=True)
class StatementLine:
    timestamp: object
    journal_id: int
    operation_reference: str
    operation_type: str  # TRANSFER | PAYMENT | CARD | JOURNAL
    description: str
    counterparty: str
    direction: str  # IN | OUT (from the viewed account's perspective)
    amount: Decimal
    currency: str
    status: str
    balance_after: Decimal
    source_type: str
    source_id: int | None


def _source_maps(journal_ids):
    """Batched reverse lookup journal -> originating operation."""
    from apps.cards.models import CardTransaction
    from apps.payments.models import Payment
    from apps.transfers.models import Transfer

    transfers = {
        t.journal_id: t
        for t in Transfer.objects.filter(journal_id__in=journal_ids)
        .select_related("destination_account__customer", "beneficiary")
    }
    payments = {p.journal_id: p for p in Payment.objects.filter(journal_id__in=journal_ids).select_related("bill")}
    cards = {c.journal_id: c for c in CardTransaction.objects.filter(journal_id__in=journal_ids)}
    return transfers, payments, cards


def _counterparty(account, transfers, payments, cards):
    def resolve(journal_id):
        t = transfers.get(journal_id)
        if t is not None:
            if t.source_account_id == account.pk and t.destination_account_id:
                return t.destination_account.customer.get_full_name()
            if t.beneficiary_id:
                return t.beneficiary.name
            return "External"
        p = payments.get(journal_id)
        if p is not None:
            return p.bill.biller
        c = cards.get(journal_id)
        if c is not None:
            return c.merchant
        return "—"

    return resolve


def statement_queryset(account):
    """Ordered, deterministic queryset of the account's posted ledger lines
    annotated with a signed amount and running balance_after.

    Ordering key: (posted_at, journal_id, entry id) — stable across requests.
    Customer accounts are LIABILITY: CREDIT raises the balance.
    """
    signed = Case(
        When(side="CREDIT", then=F("amount")),
        When(side="DEBIT", then=-F("amount")),
        output_field=models_DecimalField(max_digits=19, decimal_places=2),
    )
    order_by = (
        F("journal__posted_at").asc(),
        F("journal_id").asc(),
        F("id").asc(),
    )
    running = Window(
        Sum(Coalesce(signed, Value(Decimal("0")))), order_by=order_by
    )
    qs = (
        LedgerEntry.objects.filter(
            account=account.ledger_account,
            journal__status=JournalEntry.Status.POSTED,
            journal__currency=account.currency,
        )
        .select_related("journal")
        .annotate(signed_amount=signed, balance_after=running)
        .order_by(*order_by)
    )
    return qs


def statement_lines(account, page_entries):
    """Project a page of the queryset into StatementLine DTOs (batched lookups)."""
    entries = list(page_entries)
    journal_ids = [e.journal_id for e in entries]
    transfers, payments, cards = _source_maps(journal_ids)
    resolve_cp = _counterparty(account, transfers, payments, cards)

    lines = []
    for e in entries:
        j = e.journal
        if j.id in transfers:
            op_type, source_type, source = "TRANSFER", "transfer", transfers[j.id]
            source_id = source.pk
        elif j.id in payments:
            op_type, source_type, source = "PAYMENT", "payment", payments[j.id]
            source_id = source.pk
        elif j.id in cards:
            op_type, source_type, source = "CARD", "card", cards[j.id]
            source_id = source.pk
        else:
            op_type, source_type, source, source_id = "JOURNAL", "journal", j, None

        direction = "IN" if e.side == "CREDIT" else "OUT"
        lines.append(
            StatementLine(
                timestamp=j.posted_at,
                journal_id=j.id,
                operation_reference=j.reference,
                operation_type=op_type,
                description=j.description,
                counterparty=resolve_cp(j.id),
                direction=direction,
                amount=e.amount,
                currency=j.currency,
                status=j.status,
                balance_after=e.balance_after,
                source_type=source_type,
                source_id=source_id,
            )
        )
    return lines


def get_owned_account(user, account_id):
    """Authorization boundary: only the owner's accounts are visible."""
    return user.accounts.get(pk=account_id)


def closing_balance_matches(account, last_balance_after):
    """Reconciliation helper: statement tail must equal the balance service."""
    from apps.ledger.services import account_balance

    if last_balance_after is None:
        return account_balance(account.ledger_account) == Decimal("0")
    return account_balance(account.ledger_account) == last_balance_after


# ---------------------------------------------------------------------------
# FASE 5 Branch 3 — server-side filters (same queryset, no parallel query)
# ---------------------------------------------------------------------------

PERIODS = ("today", "7d", "30d", "month", "custom")


def _direction_sides(account):
    """Ledger sides that mean money ENTERING this account."""
    if account.ledger_account.type in ("ASSET", "EXPENSE"):
        return ("DEBIT",)
    return ("CREDIT",)


def apply_filters(qs, account, params):
    """Apply period/direction/source/search filters from validated GET params.

    Returns (queryset, active_filters). Invalid input degrades to no-op,
    never to a broader scope than requested.
    """
    from datetime import date, datetime, time
    from django.utils import timezone as tz

    filters = {}
    period = params.get("period", "")
    if period == "custom":
        try:
            start = datetime.strptime(params.get("from", ""), "%Y-%m-%d").date()
            end = datetime.strptime(params.get("to", ""), "%Y-%m-%d").date()
        except ValueError:
            return qs, filters
        if start > end:
            return qs, filters
        qs = qs.filter(
            journal__posted_at__gte=tz.make_aware(datetime.combine(start, time.min)),
            journal__posted_at__lt=tz.make_aware(datetime.combine(end, time.min)) + tz.timedelta(days=1),
        )
        filters.update(period="custom", **{"from": start.isoformat(), "to": end.isoformat()})
    elif period in PERIODS:
        now = tz.localtime()
        if period == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            qs = qs.filter(journal__posted_at__gte=start)
        elif period == "7d":
            qs = qs.filter(journal__posted_at__gte=now - tz.timedelta(days=7))
        elif period == "30d":
            qs = qs.filter(journal__posted_at__gte=now - tz.timedelta(days=30))
        elif period == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            qs = qs.filter(journal__posted_at__gte=start)
        filters["period"] = period

    direction = params.get("direction", "")
    if direction == "in":
        qs = qs.filter(side__in=_direction_sides(account))
        filters["direction"] = direction
    elif direction == "out":
        sides = ("CREDIT",) if _direction_sides(account) == ("DEBIT",) else ("DEBIT",)
        qs = qs.filter(side__in=sides)
        filters["direction"] = direction

    source = params.get("source", "")
    if source in ("TRANSFER", "PAYMENT", "CARD"):
        from apps.cards.models import CardTransaction
        from apps.payments.models import Payment
        from apps.transfers.models import Transfer
        model = {"TRANSFER": Transfer, "PAYMENT": Payment, "CARD": CardTransaction}[source]
        qs = qs.filter(journal_id__in=model.objects.values("journal_id"))
        filters["source"] = source
    elif source == "OTHER":
        from apps.cards.models import CardTransaction
        from apps.payments.models import Payment
        from apps.transfers.models import Transfer
        known = set(Transfer.objects.exclude(journal=None).values_list("journal_id", flat=True))
        known |= set(Payment.objects.exclude(journal=None).values_list("journal_id", flat=True))
        known |= set(CardTransaction.objects.exclude(journal=None).values_list("journal_id", flat=True))
        qs = qs.exclude(journal_id__in=known)
        filters["source"] = source

    term = (params.get("q") or "").strip()
    if term:
        # searchable: only fields actually stored on the journal row
        from django.db.models import Q
        qs = qs.filter(Q(journal__reference__icontains=term) | Q(journal__description__icontains=term))
        filters["q"] = term

    return qs.distinct(), filters
