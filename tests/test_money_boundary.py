"""Money boundary: prove that no business module mutates balances directly
and that every money movement produces balanced ledger journals."""
import re
from decimal import Decimal
from pathlib import Path

import pytest

from apps.accounts.models import Account
from apps.audit.models import AuditLog
from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry

APPS_DIR = Path(__file__).resolve().parent.parent / "apps"

# patterns that would indicate direct balance mutation in business code
FORBIDDEN_PATTERNS = [
    (r"\.current_balance\s*=[^=]", "direct write to current_balance"),
    (r"\.available_balance\s*=[^=]", "direct write to available_balance"),
    (r"\bbalance\s*=\s*[^=].*\.update\(", "queryset update of balance field"),
    (r"\.update\([^)]*\bbalance\b", "queryset update touching balance"),
]


def test_no_direct_balance_mutation_patterns_in_apps():
    """Static scan: business modules must never assign or update balances."""
    offenders = []
    for py in APPS_DIR.rglob("*.py"):
        if "/migrations/" in str(py) or py.name == "models.py":
            continue  # models define fields, not mutations
        src = py.read_text()
        for pattern, why in FORBIDDEN_PATTERNS:
            if re.search(pattern, src):
                offenders.append(f"{py.relative_to(APPS_DIR)}: {why}")
    assert offenders == []


def test_account_has_no_writable_balance_column():
    """The operational Account model must not store balances at all."""
    field_names = {f.name for f in Account._meta.get_fields()}
    assert "balance" not in field_names
    assert "current_balance" not in field_names


def test_transfer_produces_balanced_journal_and_audit(alice, bob):
    """Full money path: operation -> service -> ledger -> audit."""
    from apps.transfers.services import execute_transfer

    transfer, _created = execute_transfer(
        actor=alice,
        source_account_id=alice.checking.id,
        amount=Decimal("25.00"),
        destination_account_id=bob.checking.id,
        description="boundary test",
        idempotency_key="MB-E2E-1",
    )
    assert transfer.status == "COMPLETED"
    j = JournalEntry.objects.get(reference=transfer.reference)
    assert j.status == "POSTED"
    d, c = j.balance_check()
    assert d == c == Decimal("25.00")
    # customer accounts are liabilities: sender debited (liability down),
    # receiver credited (liability up)
    sides = {(e.account_id, e.side) for e in j.entries.all()}
    assert (alice.checking.ledger_account_id, "DEBIT") in sides
    assert (bob.checking.ledger_account_id, "CREDIT") in sides
    assert AuditLog.objects.filter(action="TRANSFER_COMPLETED", resource_id=transfer.id).exists()


@pytest.mark.django_db
def test_all_posted_journals_globally_balanced():
    """Global invariant across the entire ledger, whatever wrote it."""
    for j in JournalEntry.objects.filter(status="POSTED"):
        d, c = j.balance_check()
        assert d == c, f"journal {j.reference} unbalanced"
