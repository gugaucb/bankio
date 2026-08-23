"""Disaster and recovery: integrity survives interruptions."""
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.ledger import anchor_service, proof_batches, services as ledger
from apps.ledger.anchor_service import SimulatedBlockchainAnchorProvider
from apps.ledger.models import JournalEntry, LedgerAnchor, LedgerProofBatch


@pytest.fixture
def accounts(db):
    return (
        ledger.get_or_create_account("RV-CASH", "Cash", type="ASSET"),
        ledger.get_or_create_account("RV-REV", "Revenue", "INCOME"),
    )


def _post(cash, rev, amount="10.00"):
    return ledger.post_journal(
        f"RV-{uuid4().hex[:12]}", "x",
        [(cash, "DEBIT", Decimal(amount)), (rev, "CREDIT", Decimal(amount))],
    )


def test_interrupted_anchor_worker_recovers(accounts):
    """Worker dies after submit; a later run still reaches ANCHORED."""
    cash, rev = accounts
    j = _post(cash, rev)
    batch = proof_batches.seal_batch()
    provider = SimulatedBlockchainAnchorProvider()
    anchor = anchor_service.anchor_batch(batch, provider)
    assert anchor.status == LedgerAnchor.Status.SUBMITTED

    # ... worker interruption happens here (no polling) ...

    anchor = anchor_service.confirm_anchor(anchor, provider)
    for _ in range(5):
        if anchor.status == LedgerAnchor.Status.CONFIRMED:
            break
        anchor = anchor_service.confirm_anchor(anchor, provider)
    assert anchor.status == LedgerAnchor.Status.CONFIRMED
    batch.refresh_from_db()
    assert batch.status == LedgerProofBatch.Status.ANCHORED


def test_signer_unavailable_leaves_no_partial_batch(accounts, monkeypatch):
    """If signing fails, sealing must be atomic — no half-sealed batch."""
    from apps.ledger import proof_batches as pb

    class BrokenSigner:
        def sign(self, payload):
            raise RuntimeError("signature provider unavailable")

    monkeypatch.setattr(pb, "default_signer", lambda: BrokenSigner())
    cash, rev = accounts
    _post(cash, rev)
    count_before = LedgerProofBatch.objects.count()
    with pytest.raises(RuntimeError):
        proof_batches.seal_batch()
    assert LedgerProofBatch.objects.count() == count_before


def test_delayed_confirmation_stays_pending(accounts):
    """Anchors do not become VERIFIED merely by being submitted."""
    cash, rev = accounts
    _post(cash, rev)
    batch = proof_batches.seal_batch()
    provider = SimulatedBlockchainAnchorProvider()
    anchor = anchor_service.anchor_batch(batch, provider)
    anchor = anchor_service.confirm_anchor(anchor, provider)  # one poll: not enough
    assert anchor.status in (LedgerAnchor.Status.SUBMITTED, LedgerAnchor.Status.CONFIRMING)
    batch.refresh_from_db()
    assert batch.status == LedgerProofBatch.Status.SEALED  # not yet ANCHORED


def test_posting_after_full_recovery_cycle(accounts):
    """Financial posting keeps working regardless of proof-layer state."""
    cash, rev = accounts
    j1 = _post(cash, rev)
    proof_batches.seal_batch()  # sealed, never anchored
    j2 = _post(cash, rev)       # posting unaffected
    assert j1.status == j2.status == "POSTED"
    assert ledger.account_balance(cash) >= Decimal("20.00")
