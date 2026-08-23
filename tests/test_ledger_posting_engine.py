"""Posting engine tests: account status, currency integrity, side validation."""
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry


@pytest.fixture
def accounts(db):
    cash = ledger.get_or_create_account("PE-CASH", "Cash", type="ASSET")
    rev = ledger.get_or_create_account("PE-REV", "Revenue", type="INCOME")
    return cash, rev


def test_blocked_account_posting_rejected(accounts):
    cash, rev = accounts
    cash.status = cash.Status.BLOCKED
    cash.save()
    with pytest.raises(ValueError, match="BLOCKED"):
        ledger.post_journal("PE-BLK", "x", [(cash, "DEBIT", "10.00"), (rev, "CREDIT", "10.00")])


def test_closed_account_posting_rejected(accounts):
    cash, rev = accounts
    rev.status = rev.Status.CLOSED
    rev.save()
    with pytest.raises(ValueError, match="CLOSED"):
        ledger.post_journal("PE-CLS", "x", [(cash, "DEBIT", "10.00"), (rev, "CREDIT", "10.00")])


def test_cross_currency_posting_rejected(accounts):
    cash, _ = accounts
    eur = ledger.get_or_create_account("PE-EUR", "EUR Cash", type="ASSET", currency="EUR")
    with pytest.raises(ValueError, match="currencies"):
        ledger.post_journal(
            "PE-FX", "x", [(cash, "DEBIT", "10.00"), (eur, "CREDIT", "10.00")]
        )


def test_explicit_currency_mismatch_rejected(accounts):
    cash, rev = accounts
    with pytest.raises(ValueError, match="currency"):
        ledger.post_journal(
            "PE-FX2", "x", [(cash, "DEBIT", "10.00"), (rev, "CREDIT", "10.00")], currency="EUR"
        )


def test_invalid_side_rejected(accounts):
    cash, rev = accounts
    with pytest.raises(ValueError, match="side"):
        ledger.post_journal("PE-SIDE", "x", [(cash, "SIDEWAYS", "10.00"), (rev, "CREDIT", "10.00")])


def test_empty_journal_rejected(accounts):
    cash, rev = accounts
    with pytest.raises(ValueError, match="no postings"):
        ledger.post_journal("PE-EMPTY", "x", [])


def test_journal_records_currency(accounts):
    cash, rev = accounts
    j = ledger.post_journal("PE-CUR", "x", [(cash, "DEBIT", "5.00"), (rev, "CREDIT", "5.00")])
    assert j.currency == "USD"


def test_failed_posting_leaves_no_entries(accounts):
    """A rejected posting must leave zero partial rows behind."""
    from apps.ledger.models import LedgerEntry

    cash, rev = accounts
    before = JournalEntry.objects.count()
    eur = ledger.get_or_create_account("PE-EUR2", "EUR Cash", type="ASSET", currency="EUR")
    with pytest.raises(ValueError):
        ledger.post_journal("PE-PARTIAL", "x", [(cash, "DEBIT", "1.00"), (eur, "CREDIT", "1.00")])
    assert JournalEntry.objects.count() == before
    assert not LedgerEntry.objects.filter(account__code__in=["PE-CASH", "PE-EUR2"]).exists()


amounts = st.decimals(min_value="0.01", max_value="1000000.00", places=2)
small_amounts = st.decimals(min_value="0.01", max_value="500.00", places=2)


@settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(a1=amounts, a2=small_amounts)
def test_property_multiline_journals_always_balance(a1, a2, accounts):
    """Arbitrary multi-line balanced journals must post and stay balanced."""
    cash, rev = accounts
    fee = ledger.get_or_create_account("PE-FEE", "Fees", type="EXPENSE")
    # debit legs total a1 + a2; credit leg mirrors exactly
    lines = [
        (cash, "DEBIT", a1),
        (fee, "DEBIT", a2),
        (rev, "CREDIT", a1 + a2),
    ]
    ref = f"PE-PROP-{a1}-{a2}"
    j = ledger.post_journal(ref, "prop", lines)
    d, c = j.balance_check()
    assert d == c == a1 + a2
