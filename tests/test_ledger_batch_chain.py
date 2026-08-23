"""Batch chain: continuity between sealed Merkle batches."""
from decimal import Decimal
from uuid import uuid4

import pytest
from django.core.management import call_command

from apps.ledger import proof_batches, services as ledger
from apps.ledger.models import LedgerProofBatch


@pytest.fixture
def accounts(db):
    cash = ledger.get_or_create_account("BC-CASH", "Cash", type="ASSET")
    rev = ledger.get_or_create_account("BC-REV", "Revenue", type="INCOME")
    return cash, rev


def _post(cash, rev, amount="10.00"):
    return ledger.post_journal(
        f"BC-{uuid4().hex[:10]}", "x",
        [(cash, "DEBIT", Decimal(amount)), (rev, "CREDIT", Decimal(amount))],
    )


def test_chain_of_three_batches_valid(accounts):
    cash, rev = accounts
    for _ in range(3):
        _post(cash, rev)
        proof_batches.seal_batch()
    out = __import__("io").StringIO()
    call_command("verify_batch_chain", stdout=out)
    assert "BATCH CHAIN VALID" in out.getvalue()


def test_out_of_order_batch_detected(accounts):
    cash, rev = accounts
    _post(cash, rev)
    b1 = proof_batches.seal_batch()
    _post(cash, rev)
    b2 = proof_batches.seal_batch()
    # simulate an attacker rewriting b2's linkage
    LedgerProofBatch.objects.filter(pk=b2.pk).update(previous_batch_hash="d" * 64)
    out = __import__("io").StringIO()
    try:
        call_command("verify_batch_chain", stdout=out)
    except SystemExit:
        pass
    assert "INVALID" in out.getvalue()


def test_modified_root_breaks_signature(accounts):
    cash, rev = accounts
    _post(cash, rev)
    b = proof_batches.seal_batch()
    LedgerProofBatch.objects.filter(pk=b.pk).update(merkle_root="e" * 64)
    out = __import__("io").StringIO()
    try:
        call_command("verify_batch_chain", stdout=out)
    except SystemExit:
        pass
    assert "INVALID" in out.getvalue()
