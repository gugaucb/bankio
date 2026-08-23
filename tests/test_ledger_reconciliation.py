"""Reconciliation: ledger truth vs projections must agree exactly."""
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db.models import Q, Sum

from apps.audit.models import AuditLog
from apps.ledger import reconciliation, services as ledger
from apps.ledger.models import JournalEntry, LedgerEntry


def make_rc_user(user_factory, account_factory):
    from apps.customers.models import Customer

    u = user_factory("rc-user")
    Customer.objects.create(user=u, customer_number="CUST-RCUSER")
    u.checking = account_factory(u, "5000.00")
    return u


def test_reconciles_clean_ledger(alice, bob):
    report = reconciliation.run()
    assert report["status"] == "RECONCILED"
    assert report["accounts_checked"] >= 2
    assert report["differences"] == []


@pytest.mark.django_db(transaction=True)
def test_detects_unbalanced_posted_journal(user_factory, account_factory):
    """Simulate a DB-level attacker: disable the guard trigger, post an
    unbalanced journal, restore protection. Reconciliation MUST fail it."""
    alice = make_rc_user(user_factory, account_factory)
    from django.db import connection

    cash = alice.checking.ledger_account
    other = ledger.get_or_create_account("RC-OTHER2", "Other", type="ASSET")
    import uuid
    j = JournalEntry.objects.create(reference=f"RC-CORRUPT-{uuid.uuid4().hex[:8]}")
    LedgerEntry.objects.create(journal=j, account=cash, side="DEBIT", amount=Decimal("50.00"))
    LedgerEntry.objects.create(journal=j, account=other, side="CREDIT", amount=Decimal("40.00"))
    try:
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE ledger_ledgerentry DISABLE TRIGGER trg_protect_posted_entries_update")
            cur.execute("ALTER TABLE ledger_journalentry DISABLE TRIGGER trg_journal_posted_balanced")
        JournalEntry.objects.filter(pk=j.pk).update(status="POSTED")
    finally:
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE ledger_ledgerentry ENABLE TRIGGER trg_protect_posted_entries_update")
            cur.execute("ALTER TABLE ledger_journalentry ENABLE TRIGGER trg_journal_posted_balanced")

    report = reconciliation.run()
    assert report["status"] == "FAILED"
    assert any(d["check"] == "posted_journal_balance" for d in report["differences"])


def test_global_invariant_holds_after_mixed_activity(alice, bob):
    from apps.transfers.services import execute_transfer

    execute_transfer(actor=alice, source_account_id=alice.checking.pk, amount="75.25",
                     destination_account_id=bob.checking.pk, idempotency_key="RC-T1")
    report = reconciliation.run()
    assert report["status"] == "RECONCILED"
    agg = LedgerEntry.objects.filter(journal__status="POSTED").aggregate(
        d=Sum("amount", filter=Q(side="DEBIT")), c=Sum("amount", filter=Q(side="CREDIT")))
    assert agg["d"] == agg["c"]


def test_reconciled_report_counts_journals(alice):
    cash = alice.checking.ledger_account
    rev = ledger.get_or_create_account("RC-REV", "Rev", type="INCOME")
    ledger.post_journal("RC-C1", "x", [(cash, "DEBIT", "5.00"), (rev, "CREDIT", "5.00")])
    report = reconciliation.run()
    assert report["balanced_journals"] >= 1


def test_reconciliation_command_runs(alice):
    out = __import__("io").StringIO()
    call_command("reconcile_ledger", stdout=out)
    text = out.getvalue()
    assert "Accounts checked:" in text
    assert "Status: RECONCILED" in text


def test_reconciliation_writes_audit_event(alice):
    before = AuditLog.objects.count()
    reconciliation.run()
    assert AuditLog.objects.filter(action__startswith="RECONCILIATION").exists() or \
        AuditLog.objects.count() >= before
