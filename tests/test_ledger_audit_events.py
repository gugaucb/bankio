"""Audit trail for ledger and proof lifecycle."""
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.audit.models import AuditLog
from apps.ledger import anchor_service, proof_batches, services as ledger
from apps.ledger.anchor_service import SimulatedBlockchainAnchorProvider
from apps.ledger.models import JournalEntry, LedgerProofBatch


@pytest.fixture
def accounts(db):
    return (
        ledger.get_or_create_account("AU-CASH", "Cash", type="ASSET"),
        ledger.get_or_create_account("AU-REV", "Revenue", "INCOME"),
    )


def test_posting_and_reversal_are_audited(accounts):
    cash, rev = accounts
    j = ledger.post_journal(
        f"AU-{uuid4().hex[:8]}", "x",
        [(cash, "DEBIT", "5.00"), (rev, "CREDIT", "5.00")],
    )
    assert AuditLog.objects.filter(action="JOURNAL_POSTED", resource_id=j.pk).exists()
    r = ledger.reverse_journal(j)
    assert AuditLog.objects.filter(action="JOURNAL_REVERSED", resource_id=j.pk).exists()
    assert AuditLog.objects.filter(action="JOURNAL_POSTED", resource_id=r.pk).exists()


def test_seal_is_audited(accounts):
    cash, rev = accounts
    ledger.post_journal(
        f"AU-S-{uuid4().hex[:8]}", "x",
        [(cash, "DEBIT", "1.00"), (rev, "CREDIT", "1.00")],
    )
    batch = proof_batches.seal_batch()
    assert AuditLog.objects.filter(action="PROOF_BATCH_SEALED", resource_id=batch.pk).exists()


def test_anchor_lifecycle_audited_without_secrets(accounts):
    cash, rev = accounts
    ledger.post_journal(
        f"AU-A-{uuid4().hex[:8]}", "x",
        [(cash, "DEBIT", "1.00"), (rev, "CREDIT", "1.00")],
    )
    batch = proof_batches.seal_batch()
    provider = SimulatedBlockchainAnchorProvider()
    anchor = anchor_service.anchor_batch(batch, provider)
    for _ in range(5):
        anchor_service.confirm_anchor(anchor, provider)
        if anchor.status == LedgerProofBatch.Status.ANCHORED:
            break
    submitted = AuditLog.objects.get(action="BLOCKCHAIN_ANCHOR_SUBMITTED")
    confirmed = AuditLog.objects.get(action="BLOCKCHAIN_ANCHOR_CONFIRMED")
    blob = str(submitted.metadata) + str(confirmed.metadata) + str(submitted.__dict__)
    assert "private" not in blob.lower() and "secret" not in blob.lower()
