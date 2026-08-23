"""Financial reconciliation: prove operational balances agree with ledger truth.

The ledger is the source of truth; anything derived from it must reconcile
to zero difference. Any unexplained difference is a critical condition and
must never be silently corrected here.
"""
from decimal import Decimal

from django.db.models import Q, Sum

from apps.audit.services import record as audit

from .models import JournalEntry, LedgerEntry

RECONCILED = "RECONCILED"
WARNING = "WARNING"
FAILED = "FAILED"


def _raw_account_balance(account):
    agg = LedgerEntry.objects.filter(
        account=account.ledger_account, journal__status=JournalEntry.Status.POSTED
    ).aggregate(
        debits=Sum("amount", filter=Q(side="DEBIT")),
        credits=Sum("amount", filter=Q(side="CREDIT")),
    )
    d = agg["debits"] or Decimal("0")
    c = agg["credits"] or Decimal("0")
    return d - c if account.ledger_account.type in ("ASSET", "EXPENSE") else c - d


def run():
    """Run all reconciliation checks.

    Returns a report dict:
      status: RECONCILED | WARNING | FAILED
      accounts_checked, balanced_journals, differences: [..]
    Never mutates financial data.
    """
    differences = []

    # 1. every posted journal balances
    unbalanced = []
    for j in JournalEntry.objects.filter(status="POSTED"):
        d, c = j.balance_check()
        if d != c:
            unbalanced.append({"journal": j.reference, "debits": str(d), "credits": str(c)})
    if unbalanced:
        differences.append({"check": "posted_journal_balance", "items": unbalanced})

    # 2. global invariant: total debits == total credits over posted entries
    agg = LedgerEntry.objects.filter(journal__status="POSTED").aggregate(
        debits=Sum("amount", filter=Q(side="DEBIT")),
        credits=Sum("amount", filter=Q(side="CREDIT")),
    )
    total_d = agg["debits"] or Decimal("0")
    total_c = agg["credits"] or Decimal("0")
    if total_d != total_c:
        differences.append({
            "check": "global_debits_credits",
            "debits": str(total_d), "credits": str(total_c),
        })

    # 3. per-customer-account projection check (service layer vs raw SQL-level aggregate)
    from apps.accounts.models import Account

    accounts_checked = 0
    mismatched = []
    for acct in Account.objects.select_related("ledger_account").all():
        accounts_checked += 1
        expected = _raw_account_balance(acct)
        projected = acct.current_balance
        if expected != projected:
            mismatched.append({
                "account": acct.account_number,
                "ledger_raw": str(expected),
                "projection": str(projected),
            })
        if acct.blocked_amount < 0:
            mismatched.append({"account": acct.account_number, "issue": "negative blocked_amount"})
    if mismatched:
        differences.append({"check": "account_projection", "items": mismatched})

    # 4. orphaned entries (entry without posted journal is impossible via app,
    #    but drafts older than existence of posting engine would show up here)
    draft_entries = LedgerEntry.objects.filter(journal__status="DRAFT").count()
    if draft_entries:
        differences.append({"check": "draft_entries_present", "count": draft_entries})

    if any(d["check"] in ("posted_journal_balance", "global_debits_credits", "account_projection")
           for d in differences):
        status = FAILED
    elif differences:
        status = WARNING
    else:
        status = RECONCILED

    report = {
        "status": status,
        "accounts_checked": accounts_checked,
        "balanced_journals": JournalEntry.objects.filter(status="POSTED").count() - len(unbalanced),
        "differences": differences,
    }

    if status == FAILED:
        audit(action="RECONCILIATION_FAILED", resource=None, metadata={"report": report})
    else:
        audit(action="RECONCILIATION_RUN", resource=None, metadata={"status": status})
    return report
