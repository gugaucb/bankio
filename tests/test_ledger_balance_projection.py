"""Balance projection tests: balances are pure ledger derivations and can
always be rebuilt from POSTED journals alone."""
from decimal import Decimal

import pytest
from django.db.models import Q, Sum

from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry, LedgerEntry


@pytest.fixture
def accounts(db):
    cash = ledger.get_or_create_account("BP-CASH", "Cash", type="ASSET")
    rev = ledger.get_or_create_account("BP-REV", "Revenue", type="INCOME")
    return cash, rev


def _raw_posted_balance(account):
    """Independent recomputation straight from the entries table."""
    agg = LedgerEntry.objects.filter(
        account=account, journal__status="POSTED"
    ).aggregate(
        debits=Sum("amount", filter=Q(side="DEBIT")),
        credits=Sum("amount", filter=Q(side="CREDIT")),
    )
    d = agg["debits"] or Decimal("0")
    c = agg["credits"] or Decimal("0")
    return d - c if account.type in ("ASSET", "EXPENSE") else c - d


def test_draft_journal_does_not_affect_balance(accounts):
    """Uncommitted drafts are not financial facts yet."""
    cash, rev = accounts
    j = JournalEntry.objects.create(reference="BP-DRAFT")
    LedgerEntry.objects.create(journal=j, account=cash, side="DEBIT", amount=Decimal("500.00"))
    LedgerEntry.objects.create(journal=j, account=rev, side="CREDIT", amount=Decimal("500.00"))
    assert ledger.account_balance(cash) == Decimal("0")


def test_rebuild_matches_independent_computation(accounts):
    cash, rev = accounts
    for i in range(4):
        ledger.post_journal(
            f"BP-R{i}", "x",
            [(cash, "DEBIT", Decimal(f"{25.50 + i:.2f}")), (rev, "CREDIT", Decimal(f"{25.50 + i:.2f}"))],
        )
    assert ledger.account_balance(cash) == _raw_posted_balance(cash)
    assert ledger.account_balance(rev) == _raw_posted_balance(rev)


def test_liability_normality(accounts):
    """LIABILITY accounts are credit-normal: credits minus debits."""
    cash, rev = accounts
    liab = ledger.get_or_create_account("BP-LIAB", "Customer Liability", type="LIABILITY")
    bank_asset = ledger.get_or_create_account("BP-BANK", "Bank Asset", type="ASSET")
    ledger.post_journal("BP-L1", "deposit", [(bank_asset, "DEBIT", "100.00"), (liab, "CREDIT", "100.00")])
    assert ledger.account_balance(liab) == Decimal("100.00")
    # a withdrawal flips normality correctly
    ledger.post_journal("BP-L2", "withdrawal", [(liab, "DEBIT", "40.00"), (bank_asset, "CREDIT", "40.00")])
    assert ledger.account_balance(liab) == Decimal("60.00")
    assert ledger.account_balance(bank_asset) == Decimal("60.00")


def test_projection_survives_full_recompute_after_activity(accounts):
    """Simulated cutover: drop every derived value (there are none stored),
    recompute from scratch, compare against expected arithmetic."""
    cash, rev = accounts
    expected = Decimal("0")
    for i, amt in enumerate(["10.10", "20.20", "30.30"]):
        ledger.post_journal(f"BP-F{i}", "x", [(cash, "DEBIT", amt), (rev, "CREDIT", amt)])
        expected += Decimal(amt)
    r = ledger.reverse_journal(JournalEntry.objects.get(reference="BP-F2"))
    expected -= Decimal("30.30")
    assert ledger.account_balance(cash) == expected == Decimal("30.30")
    assert _raw_posted_balance(cash) == expected
