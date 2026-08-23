"""Merkle batches and inclusion proofs."""
from decimal import Decimal

import pytest

from apps.ledger import merkle, proof_batches, services as ledger
from apps.ledger.models import JournalEntry, LedgerProofBatch


@pytest.fixture
def accounts(db):
    cash = ledger.get_or_create_account("MK-CASH", "Cash", type="ASSET")
    rev = ledger.get_or_create_account("MK-REV", "Revenue", type="INCOME")
    return cash, rev


def _post_n(cash, rev, n):
    import uuid

    journals = []
    for i in range(n):
        journals.append(
            ledger.post_journal(
                f"MK-{uuid.uuid4().hex[:10]}", "x",
                [(cash, "DEBIT", Decimal(f"{10 + i}.00")), (rev, "CREDIT", Decimal(f"{10 + i}.00"))],
            )
        )
    return journals


def test_merkle_root_deterministic():
    leaves = [f"h{i:02d}" for i in range(8)]
    assert merkle.merkle_root(leaves) == merkle.merkle_root(leaves)


def test_single_leaf_root_is_leaf_hash():
    root = merkle.merkle_root(["abc"])
    assert root == merkle.leaf_hash("abc")


def test_odd_leaf_count_duplicates_last():
    leaves3 = ["a", "b", "c"]
    leaves4 = ["a", "b", "c", "c"]
    assert merkle.merkle_root(leaves3) == merkle.merkle_root(leaves4)


def test_large_batch_root_changes_with_any_leaf():
    leaves = [f"leaf-{i}" for i in range(100)]
    r1 = merkle.merkle_root(leaves)
    leaves[57] = "changed"
    r2 = merkle.merkle_root(leaves)
    assert r1 != r2


def test_valid_proof_accepts_and_invalid_rejects():
    leaves = [f"x{i}" for i in range(5)]
    root = merkle.merkle_root(leaves)
    for idx in range(5):  # every position, including odd-count duplicate path
        proof = merkle.generate_proof(leaves, idx)
        assert merkle.verify_proof(leaves[idx], proof, root)
        # tampering with the leaf fails
        assert not merkle.verify_proof("evil", proof, root)
        # tampering with a proof step fails
        broken = [dict(proof[0], hash="0" * 64)] + proof[1:]
        assert not merkle.verify_proof(leaves[idx], broken, root)
        # wrong root fails
        assert not merkle.verify_proof(leaves[idx], proof, "f" * 64)


def test_seal_batch_creates_signed_commitment(accounts):
    cash, rev = accounts
    _post_n(cash, rev, 6)
    batch = proof_batches.seal_batch()
    assert batch is not None
    assert batch.status == LedgerProofBatch.Status.SEALED
    assert batch.entry_count == 6
    assert batch.signature is not None
    assert proof_batches.verify_batch_signature(batch) is True


def test_seal_batch_chain_links(accounts):
    cash, rev = accounts
    _post_n(cash, rev, 2)
    b1 = proof_batches.seal_batch()
    _post_n(cash, rev, 2)
    b2 = proof_batches.seal_batch()
    assert b2.sequence == b1.sequence + 1
    assert b2.previous_batch_hash == b1.batch_manifest_hash


def test_inclusion_proof_roundtrip(accounts):
    cash, rev = accounts
    journals = _post_n(cash, rev, 3)
    batch = proof_batches.seal_batch()
    victim = journals[1]
    got_batch, proof = proof_batches.generate_merkle_proof(victim)
    assert got_batch.id == batch.id
    assert merkle.verify_proof(victim.payload_hash, proof, batch.merkle_root) is True


def test_changed_journal_invalidates_proof(accounts):
    """A modified leaf breaks its own proof (PROOF INVARIANT 3)."""
    cash, rev = accounts
    journals = _post_n(cash, rev, 2)
    batch = proof_batches.seal_batch()
    _, proof = proof_batches.generate_merkle_proof(journals[0])
    assert merkle.verify_proof(journals[0].payload_hash, proof, batch.merkle_root)
    assert not merkle.verify_proof("f" * 64, proof, batch.merkle_root)


def test_unbatched_journal_has_no_proof(accounts):
    cash, rev = accounts
    j = _post_n(cash, rev, 1)[0]
    with pytest.raises(proof_batches.ProofBatchError):
        proof_batches.generate_merkle_proof(j)
