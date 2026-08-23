"""Performance: posting stays fast; proof work never blocks the money path."""
import time
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry


@pytest.fixture
def accounts(db):
    return (
        ledger.get_or_create_account("PF-CASH", "Cash", type="ASSET"),
        ledger.get_or_create_account("PF-REV", "Revenue", "INCOME"),
    )


def test_posting_throughput(accounts):
    """100 balanced postings must complete in a bounded time (ACID, no crypto waits)."""
    cash, rev = accounts
    start = time.monotonic()
    for _ in range(100):
        ledger.post_journal(
            f"PF-{uuid4().hex[:12]}", "perf",
            [(cash, "DEBIT", "1.00"), (rev, "CREDIT", "1.00")],
        )
    elapsed = time.monotonic() - start
    assert JournalEntry.objects.filter(status="POSTED").count() >= 100
    # generous bound for CI variance: < 25s total (~250ms per posting)
    assert elapsed < 25, f"posting too slow: {elapsed:.2f}s"


def test_posting_never_touches_anchor_provider(accounts, monkeypatch):
    """The synchronous banking path must not wait for external anchoring."""
    from apps.ledger import anchor_service

    def explode(*a, **kw):
        raise AssertionError("anchor provider touched during posting")

    monkeypatch.setattr(anchor_service, "default_provider", explode)
    cash, rev = accounts
    j = ledger.post_journal(
        f"PF-NX-{uuid4().hex[:8]}", "x",
        [(cash, "DEBIT", "2.00"), (rev, "CREDIT", "2.00")],
    )
    assert j.status == "POSTED"


def test_batch_sealing_is_bounded(accounts):
    """Sealing a few hundred journals completes in reasonable time."""
    import time

    cash, rev = accounts
    for _ in range(200):
        ledger.post_journal(
            f"PF-S-{uuid4().hex[:12]}", "x",
            [(cash, "DEBIT", "0.50"), (rev, "CREDIT", "0.50")],
        )
    from apps.ledger import proof_batches

    start = time.monotonic()
    batch = proof_batches.seal_batch()
    elapsed = time.monotonic() - start
    assert batch is not None and batch.entry_count >= 200
    assert elapsed < 15, f"sealing too slow: {elapsed:.2f}s"
