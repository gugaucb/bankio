"""Ledger domain services: posting balanced journals, reversals, balance queries."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import JournalEntry, LedgerAccount, LedgerEntry


def get_or_create_account(code, name, type="LIABILITY", currency="USD", is_customer=False):
    return LedgerAccount.objects.get_or_create(
        code=code,
        defaults={"name": name, "type": type, "currency": currency, "is_customer_account": is_customer},
    )[0]


def account_balance(ledger_account):
    """Balance from ledger activity only — never a stored mutable column."""
    agg = LedgerEntry.objects.filter(account=ledger_account).aggregate(
        debits=Sum("amount", filter=Q(side="DEBIT")), credits=Sum("amount", filter=Q(side="CREDIT"))
    )
    debits = agg["debits"] or Decimal("0")
    credits = agg["credits"] or Decimal("0")
    if ledger_account.type in ("ASSET", "EXPENSE"):
        return debits - credits
    return credits - debits


@transaction.atomic
def post_journal(reference, description, lines, posted_at=None, currency=None):
    """
    lines: iterable of (account, 'DEBIT'|'CREDIT', Decimal amount).
    Atomically validates balance and posts. Raises ValueError when unbalanced,
    when any account is not ACTIVE, or when lines mix currencies.
    """
    if not lines:
        raise ValueError(f"Journal {reference} has no postings.")
    currencies = {account.currency for account, _, _ in lines}
    if len(currencies) > 1:
        raise ValueError(f"Journal {reference} mixes currencies: {sorted(currencies)}")
    journal_currency = next(iter(currencies))
    if currency and currency != journal_currency:
        raise ValueError(
            f"Journal {reference} declared currency {currency} != posting currency {journal_currency}"
        )

    journal = JournalEntry.objects.create(
        reference=reference, description=description, currency=journal_currency
    )
    debits = credits = Decimal("0")
    for account, side, amount in lines:
        if account.status != LedgerAccount.Status.ACTIVE:
            raise ValueError(f"Account {account.code} is {account.status}; posting refused.")
        if side not in ("DEBIT", "CREDIT"):
            raise ValueError(f"Invalid side {side!r} in journal {reference}.")
        amount = Decimal(amount)
        LedgerEntry.objects.create(journal=journal, account=account, side=side, amount=amount)
        if side == "DEBIT":
            debits += amount
        else:
            credits += amount
    if debits != credits or debits == 0:
        raise ValueError(f"Unbalanced journal {reference}: debits={debits} credits={credits}")
    journal.status = JournalEntry.Status.POSTED
    journal.posted_at = posted_at or timezone.now()
    journal.save(update_fields=["status", "posted_at"])
    return journal


@transaction.atomic
def reverse_journal(original, reference=None, description=""):
    """Create the reversing mirror journal for a posted entry."""
    if original.status != JournalEntry.Status.POSTED:
        raise ValueError("Only posted journals can be reversed.")
    if original.reversed_by.exists():
        raise ValueError("Journal already reversed.")
    ref = reference or f"REV-{original.reference}"
    lines = [
        (e.account, ("CREDIT" if e.side == "DEBIT" else "DEBIT"), e.amount)
        for e in original.entries.select_related("account")
    ]
    reversal = post_journal(ref, description or f"Reversal of {original.reference}", lines)
    reversal.reverses = original
    reversal.save(update_fields=["reverses"])
    return reversal
