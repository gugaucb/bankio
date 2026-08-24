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
