"""Adversarial suite: active attempts to break the system.

Consolidates attack vectors; many are also covered at the unit level in
test_ledger_db_constraints / test_ledger_immutability / test_proof_*.
"""
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import connection, transaction

from apps.accounts.models import Account
from apps.ledger import proof_verification, services as ledger
from apps.ledger.models import JournalEntry


@pytest.fixture
def accounts(db):
    return (
        ledger.get_or_create_account("AD-CASH", "Cash", type="ASSET"),
        ledger.get_or_create_account("AD-REV", "Revenue", "INCOME"),
    )


def _mk_account(user_factory, username):
    from apps.accounts.models import Account
    from apps.customers.models import Customer

    u = user_factory(username)
    Customer.objects.create(user=u, customer_number=f"CUST-{username[:11].upper()}")
    la = ledger.get_or_create_account(f"2001-AD-{username[:20]}", f"AD {username}", is_customer=True)
    return Account.objects.create(customer=u, account_number=username[:16].rjust(16, "9"), ledger_account=la)


def test_attacker_edits_account_row_balances_do_not_move(user_factory):
    """Balances are ledger-derived; tampering with operational rows is inert."""
    acct = _mk_account(user_factory, "ad-inert")
    before = acct.current_balance
    Account.objects.filter(pk=acct.pk).update(blocked_amount=Decimal("999999"))
    acct.refresh_from_db()
    assert acct.current_balance == before  # unchanged: truth lives in the ledger


@pytest.mark.django_db(transaction=True)
def test_raw_sql_amount_rewrite_breaks_hash_chain(user_factory, account_factory):
    """Attacker disables triggers and rewrites history -> verification fails."""
    from apps.customers.models import Customer

    u = user_factory("ad-user")
    Customer.objects.create(user=u, customer_number="CUST-ADUSER")
    u.checking = account_factory(u, "1000.00")
    cash = u.checking.ledger_account
    rev = ledger.get_or_create_account("AD-REV2", "Rev", type="INCOME")
    j = ledger.post_journal(
        f"AD-{uuid4().hex[:8]}", "x",
        [(cash, "DEBIT", "100.00"), (rev, "CREDIT", "100.00")],
    )
    try:
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE ledger_ledgerentry DISABLE TRIGGER trg_protect_posted_entries_update")
            cur.execute("ALTER TABLE ledger_journalentry DISABLE TRIGGER trg_posted_journal_immutable_update")
        victim = JournalEntry.objects.filter(pk=j.pk).first()
        entry = victim.entries.first()
        LedgerEntryRaw.update_amount(entry.pk, "1.00")
    finally:
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE ledger_ledgerentry ENABLE TRIGGER trg_protect_posted_entries_update")
            cur.execute("ALTER TABLE ledger_journalentry ENABLE TRIGGER trg_posted_journal_immutable_update")

    report = proof_verification.verify_journal(j.reference)
    assert report["result"] == proof_verification.FAILED


class LedgerEntryRaw:
    @staticmethod
    def update_amount(pk, value):
        with connection.cursor() as cur:
            cur.execute("UPDATE ledger_ledgerentry SET amount = %s WHERE id = %s", [value, pk])


def test_rollback_mid_posting_leaves_no_partial_state(accounts):
    """INVARIANT 4: a crash after debit insert rolls back everything."""
    cash, rev = accounts
    ref = f"AD-RB-{uuid4().hex[:8]}"

    try:
        with transaction.atomic():
            sid = transaction.savepoint()
            journal = JournalEntry.objects.create(reference=ref)
            from apps.ledger.models import LedgerEntry

            LedgerEntry.objects.create(journal=journal, account=cash, side="DEBIT", amount=Decimal("5.00"))
            raise RuntimeError("simulated crash")
    except RuntimeError:
        pass

    assert not JournalEntry.objects.filter(reference=ref).exists()
    assert ledger.account_balance(cash) == Decimal("0")


def test_negative_blocked_amount_rejected_at_db(user_factory):
    from django.db.utils import IntegrityError

    acct = _mk_account(user_factory, "ad-neg")
    with pytest.raises(IntegrityError):
        Account.objects.filter(pk=acct.pk).update(blocked_amount=Decimal("-1"))
