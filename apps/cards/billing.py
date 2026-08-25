"""FASE 8 B5 — deterministic billing-cycle closing for credit cards.

Statements are DERIVED snapshots of the eligible CardTransactions of a
cycle (approved, ledger-posted, inside the period). The unique
(card, period_end) constraint makes closing idempotent — running it twice
never duplicates or rewrites a statement.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .models import Card, CardType, CreditStatement

# Grace period between closing and the due date.
DUE_DAYS_AFTER_CLOSE = 10


def _previous_cycle(reference):
    """(period_start, period_end) of the calendar month before `reference`."""
    first_of_month = reference.replace(day=1)
    period_end = first_of_month - timedelta(days=1)
    period_start = period_end.replace(day=1)
    return period_start, period_end


def close_card_statements(reference=None):
    """Close the previous month's cycle for every credit card.

    Returns the list of newly created CreditStatement rows. Idempotent:
    existing (card, period_end) statements are left untouched."""
    reference = reference or timezone.now().date()
    period_start, period_end = _previous_cycle(reference)
    created = []
    cards = Card.objects.filter(type__in=(CardType.CREDIT,), status__in=(
        "ACTIVE", "FROZEN", "BLOCKED", "EXPIRED"))
    for card in cards:
        total = sum(
            (t.amount for t in card.transactions.filter(
                 declined=False,
                 journal__isnull=False,
                 created_at__date__gte=period_start,
                 created_at__date__lte=period_end)),
            Decimal("0"),
        )
        if total <= 0:
            continue  # no eligible movement -> no empty invoice
        stmt, was_created = CreditStatement.objects.get_or_create(
            card=card, period_end=period_end,
            defaults={
                "period_start": period_start,
                "amount_due": total,
                "due_date": period_end + timedelta(days=DUE_DAYS_AFTER_CLOSE),
            },
        )
        if was_created:
            created.append(stmt)
    return created


def statement_composition(statement):
    """Explain how a closed statement's total was composed: the approved,
    posted transactions that fell inside its cycle. Derived on demand from
    the single source of truth (no copied movements)."""
    return statement.card.transactions.filter(
        declined=False,
        journal__isnull=False,
        created_at__date__gte=statement.period_start,
        created_at__date__lte=statement.period_end,
    ).order_by("created_at")


def open_cycle_composition(card, reference=None):
    """Eligible transactions of the current (still open) cycle."""
    today = reference or timezone.now().date()
    period_start = today.replace(day=1)
    return card.transactions.filter(
        declined=False,
        journal__isnull=False,
        created_at__date__gte=period_start,
    ).order_by("-created_at")


def open_cycle_total(card, reference=None):
    return sum((t.amount for t in open_cycle_composition(card, reference)),
               Decimal("0"))


def overdue_statements(today=None):
    """Unpaid statements past their due date (for reminders / UI badges)."""
    today = today or timezone.now().date()
    return CreditStatement.objects.filter(paid=False,
                                          due_date__lt=today).select_related("card")
