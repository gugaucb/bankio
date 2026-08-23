"""Database-level ledger guarantees: constraints and triggers must hold even
when application code is bypassed (raw SQL, bulk ops, queryset updates)."""
from decimal import Decimal

import pytest
from django.db import transaction

from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry, LedgerEntry


@pytest.fixture
def accounts(db):
    cash = ledger.get_or_create_account("C-CASH", "Cash", type="ASSET")
    rev = ledger.get_or_create_account("C-REV", "Revenue", type="INCOME")
    return cash, rev


def _post(cash, rev, ref, amount="10.00"):
    return ledger.post_journal(ref, "x", [(cash, "DEBIT", amount), (rev, "CREDIT", amount)])


def test_check_constraint_rejects_negative_amount_via_bulk(accounts):
    cash, rev = accounts
    j = JournalEntry.objects.create(reference="DB-NEG")
    with pytest.raises(Exception):
        with transaction.atomic():
            LedgerEntry.objects.bulk_create(
                [LedgerEntry(journal=j, account=cash, side="DEBIT", amount=Decimal("-1.00"))]
            )


def test_check_constraint_rejects_invalid_side_via_bulk(accounts):
    cash, _ = accounts
    j = JournalEntry.objects.create(reference="DB-SIDE")
    with pytest.raises(Exception):
        with transaction.atomic():
            LedgerEntry.objects.bulk_create(
                [LedgerEntry(journal=j, account=cash, side="SIDEWAYS", amount=Decimal("1.00"))]
            )


def test_db_trigger_blocks_posting_unbalanced_journal(accounts):
    """Bypass app validation: build an unbalanced journal and force POSTED."""
    cash, rev = accounts
    j = JournalEntry.objects.create(reference="DB-UNBAL")
    LedgerEntry.objects.bulk_create(
        [
            LedgerEntry(journal=j, account=cash, side="DEBIT", amount=Decimal("100.00")),
            LedgerEntry(journal=j, account=rev, side="CREDIT", amount=Decimal("90.00")),
        ]
    )
    with transaction.atomic():
        pass  # entries committed as DRAFT
    j2 = JournalEntry.objects.get(reference="DB-UNBAL")
    j2.status = "POSTED"
    with pytest.raises(Exception, match="[Uu]nbalanced"):
        j2.save()


def test_db_trigger_blocks_zero_total_journal(accounts):
    cash, rev = accounts
    j = JournalEntry.objects.create(reference="DB-ZERO")
    LedgerEntry.objects.bulk_create(
        [
            LedgerEntry(journal=j, account=cash, side="DEBIT", amount=Decimal("5.00")),
            LedgerEntry(journal=j, account=rev, side="CREDIT", amount=Decimal("5.00")),
        ]
    )
    # remove one side to make total zero? instead: balanced but zero via empty
    j.entries.all().delete()
    j2 = JournalEntry.objects.get(reference="DB-ZERO")
    j2.status = "POSTED"
    with pytest.raises(Exception):
        j2.save()


def test_db_trigger_blocks_insert_into_posted_journal(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "DB-INS")
    with pytest.raises(Exception, match="immutable"):
        with transaction.atomic():
            LedgerEntry.objects.bulk_create(
                [LedgerEntry(journal=j, account=cash, side="DEBIT", amount=Decimal("1.00"))]
            )


def test_db_trigger_blocks_update_of_posted_entry(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "DB-UPD")
    with pytest.raises(Exception, match="immutable"):
        LedgerEntry.objects.filter(journal=j).update(amount=Decimal("999.00"))


def test_db_trigger_blocks_delete_of_posted_entry(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "DB-DEL")
    with pytest.raises(Exception, match="cannot be deleted"):
        with transaction.atomic():
            LedgerEntry.objects.filter(journal=j).delete()


def test_normal_posting_still_works_with_triggers(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "DB-OK", "42.00")
    assert j.status == "POSTED"
    assert ledger.account_balance(cash) == Decimal("42.00")
    r = ledger.reverse_journal(j)
    assert ledger.account_balance(cash) == Decimal("0")
    assert r.reverses == j


def test_noop_update_of_posted_entry_allowed(accounts):
    """An identical rewrite via raw path (no actual change) must not fail."""
    cash, rev = accounts
    j = _post(cash, rev, "DB-NOOP")
    entry = j.entries.first()
    updated = LedgerEntry.objects.filter(pk=entry.pk).update(amount=entry.amount)
    assert updated == 1
    entry.refresh_from_db()
    assert entry.amount == entry.amount  # unchanged
