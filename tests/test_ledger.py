"""Domain tests: double-entry ledger invariants, immutability, reversals."""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry, LedgerEntry


@pytest.fixture
def accounts(db):
    cash = ledger.get_or_create_account("T-CASH", "Cash", type="ASSET")
    rev = ledger.get_or_create_account("T-REV", "Revenue", type="INCOME")
    return cash, rev


def test_balanced_journal_posts(accounts):
    cash, rev = accounts
    j = ledger.post_journal("TJ-1", "sale", [(cash, "DEBIT", "100.00"), (rev, "CREDIT", "100.00")])
    assert j.status == "POSTED"
    assert ledger.account_balance(cash) == Decimal("100.00")


def test_unbalanced_journal_rejected(accounts):
    cash, rev = accounts
    with pytest.raises(ValueError):
        ledger.post_journal("TJ-BAD", "bad", [(cash, "DEBIT", "100.00"), (rev, "CREDIT", "90.00")])


def test_zero_journal_rejected(accounts):
    cash, _ = accounts
    with pytest.raises(ValueError):
        ledger.post_journal("TJ-ZERO", "zero", [])


def test_negative_entry_rejected(accounts):
    cash, rev = accounts
    j = JournalEntry.objects.create(reference="TJ-NEG")
    with pytest.raises(ValidationError):
        LedgerEntry.objects.create(journal=j, account=cash, side="DEBIT", amount=Decimal("-5"))


def test_posted_journal_immutable(accounts):
    cash, rev = accounts
    j = ledger.post_journal("TJ-IMM", "x", [(cash, "DEBIT", "10.00"), (rev, "CREDIT", "10.00")])
    j.description = "tampered"
    with pytest.raises(ValidationError):
        j.save()
    with pytest.raises(ValidationError):
        j.delete()
    entry = j.entries.first()
    with pytest.raises(ValidationError):
        entry.delete()


def test_reversal_restores_balance(accounts):
    cash, rev = accounts
    j = ledger.post_journal("TJ-R1", "x", [(cash, "DEBIT", "50.00"), (rev, "CREDIT", "50.00")])
    r = ledger.reverse_journal(j)
    assert ledger.account_balance(cash) == Decimal("0")
    assert r.reverses == j
    with pytest.raises(ValueError):
        ledger.reverse_journal(j)  # cannot reverse twice


def test_global_invariant_all_journals_balance(accounts):
    """Every posted journal must satisfy SUM(debits)==SUM(credits)."""
    cash, rev = accounts
    for i in range(5):
        ledger.post_journal(f"TJ-G{i}", "x",
                            [(cash, "DEBIT", Decimal(10 + i)), (rev, "CREDIT", Decimal(10 + i))])
    for j in JournalEntry.objects.filter(status="POSTED"):
        d, c = j.balance_check()
        assert d == c


money = st.decimals(min_value="0.01", max_value="100000.00", places=2)


@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(amount=money)
def test_property_random_amounts_always_balance(amount, accounts):
    cash, rev = accounts
    ref = f"PROP-{amount}"
    j = ledger.post_journal(ref, "prop", [(cash, "DEBIT", amount), (rev, "CREDIT", amount)])
    d, c = j.balance_check()
    assert d == c == amount
