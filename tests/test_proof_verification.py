"""End-to-end proof verification: the full cryptographic failure matrix."""
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.ledger import (
    anchor_service,
    canonical,
    proof_batches,
    proof_verification,
    services as ledger,
)
from apps.ledger.anchor_service import SimulatedBlockchainAnchorProvider
from apps.ledger.models import JournalEntry, LedgerAnchor, LedgerProofBatch


@pytest.fixture
def accounts(db):
    cash = ledger.get_or_create_account("PV-CASH", "Cash", type="ASSET")
    rev = ledger.get_or_create_account("PV-REV", "Revenue", type="INCOME")
    return cash, rev


def _post_and_anchor(cash, rev, provider):
    j = ledger.post_journal(
        f"PV-{uuid4().hex[:10]}", "x",
        [(cash, "DEBIT", "10.00"), (rev, "CREDIT", "10.00")],
    )
    batch = proof_batches.seal_batch()
    anchor = anchor_service.anchor_batch(batch, provider)
    for _ in range(5):
        anchor_service.confirm_anchor(anchor, provider)
        if anchor.status == LedgerAnchor.Status.CONFIRMED:
            break
    return j


def test_fully_verified_journal(accounts):
    provider = SimulatedBlockchainAnchorProvider()
    j = _post_and_anchor(*accounts, provider)
    report = proof_verification.verify_journal(j.reference)
    assert report["result"] == proof_verification.VERIFIED
    steps = {s["step"] for s in report["steps"]}
    assert {"canonical_hash", "hash_chain", "merkle_proof",
            "batch_signature", "anchor_confirmation"} <= steps


def test_unanchored_journal_is_pending(accounts):
    cash, rev = accounts
    j = ledger.post_journal(
        f"PV-P-{uuid4().hex[:8]}", "x", [(cash, "DEBIT", "5.00"), (rev, "CREDIT", "5.00")]
    )
    proof_batches.seal_batch()
    report = proof_verification.verify_journal(j.reference)
    assert report["result"] == proof_verification.PENDING


def test_hash_chain_invalid_fails_proof(accounts):
    """LEDGER VALID + HASH CHAIN INVALID -> FAIL."""
    cash, rev = accounts
    j = _post_and_anchor(*accounts, SimulatedBlockchainAnchorProvider())
    JournalEntry.objects.filter(pk=j.pk).update(previous_entry_hash="1" * 64)
    report = proof_verification.verify_journal(j.reference)
    assert report["result"] == proof_verification.FAILED


def test_unknown_journal_fails(accounts):
    report = proof_verification.verify_journal("NOPE")
    assert report["result"] == proof_verification.FAILED


def test_command_output(accounts, capsys):
    from django.core.management import call_command

    provider = SimulatedBlockchainAnchorProvider()
    j = _post_and_anchor(*accounts, provider)
    call_command("verify_ledger_proof", "--journal", j.reference)
    out = capsys.readouterr().out
    assert "CRYPTOGRAPHICALLY VERIFIED" in out
