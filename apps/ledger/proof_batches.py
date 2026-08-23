"""Sealed proof batches: Merkle commitments over contiguous journal ranges."""
import json

from django.db import transaction
from django.utils import timezone

from . import canonical, merkle
from .models import JournalEntry, LedgerProofBatch
from .signing import default_signer


class ProofBatchError(Exception):
    pass


MAX_BATCH_SIZE = 10000


@transaction.atomic
def seal_batch() -> LedgerProofBatch | None:
    """Seal the next batch over posted, not-yet-batched journals.

    Membership is an immutable contiguous id range [first_journal_id,
    last_journal_id]; leaves are ordered by journal id.
    Returns None when there is nothing new to seal.
    """
    last = LedgerProofBatch.objects.order_by("-sequence").first()
    lower = (last.last_journal_id + 1) if last else 0
    journals = list(
        JournalEntry.objects.filter(status="POSTED", id__gte=lower).order_by("id")[:MAX_BATCH_SIZE]
    )
    # only seal a contiguous run; stop at any gap in ids
    selected = []
    for j in journals:
        if selected and j.id != selected[-1].id + 1:
            break
        selected.append(j)
    if not selected:
        return None

    sequence = (last.sequence + 1) if last else 1
    leaves = [j.payload_hash for j in selected]
    root = merkle.merkle_root(leaves)
    previous_batch_hash = last.batch_manifest_hash if last else canonical.GENESIS_HASH

    signer = default_signer()
    now = timezone.now()
    manifest = {
        "proof_version": canonical.PROOF_VERSION,
        "merkle_version": merkle.MERKLE_VERSION,
        "canonicalization_version": canonical.CANONICALIZATION_VERSION,
        "hash_algorithm": canonical.HASH_ALGORITHM,
        "sequence": sequence,
        "first_journal_id": selected[0].id,
        "last_journal_id": selected[-1].id,
        "entry_count": len(selected),
        "merkle_root": root,
        "previous_batch_hash": previous_batch_hash,
        "sealed_at": now.isoformat(),
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    signature = signer.sign(manifest_bytes)

    batch = LedgerProofBatch.objects.create(
        sequence=sequence,
        first_journal_id=selected[0].id,
        last_journal_id=selected[-1].id,
        entry_count=len(selected),
        merkle_root=root,
        previous_batch_hash=previous_batch_hash,
        canonicalization_version=canonical.CANONICALIZATION_VERSION,
        hash_algorithm=canonical.HASH_ALGORITHM,
        status=LedgerProofBatch.Status.SEALED,
        sealed_at=now,
        signature=signature,
        batch_manifest_hash=canonical.chain_hash(root, _manifest_digest(manifest)),
    )

    from apps.audit.services import record as audit

    audit(action="PROOF_BATCH_SEALED", resource=batch,
          metadata={"sequence": sequence, "root": root, "entries": len(selected)})
    return batch


def _manifest_digest(manifest: dict) -> str:
    import hashlib

    data = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def generate_merkle_proof(journal: JournalEntry):
    """Inclusion proof for one journal against its sealed batch."""
    batch = (
        LedgerProofBatch.objects.exclude(status=LedgerProofBatch.Status.FAILED)
        .filter(first_journal_id__lte=journal.id, last_journal_id__gte=journal.id)
        .first()
    )
    if not batch:
        raise ProofBatchError(f"Journal {journal.reference} is not in a sealed batch")
    leaves = [
        j.payload_hash
        for j in JournalEntry.objects.filter(
            status="POSTED",
            id__range=(batch.first_journal_id, batch.last_journal_id),
        ).order_by("id")
    ]
    index = journal.id - batch.first_journal_id
    proof = merkle.generate_proof(leaves, index)
    return batch, proof


def verify_batch_signature(batch: LedgerProofBatch) -> bool:
    """Verify the stored signature over the recomputed manifest."""
    signer = default_signer()
    manifest = {
        "proof_version": canonical.PROOF_VERSION,
        "merkle_version": merkle.MERKLE_VERSION,
        "canonicalization_version": batch.canonicalization_version,
        "hash_algorithm": batch.hash_algorithm,
        "sequence": batch.sequence,
        "first_journal_id": batch.first_journal_id,
        "last_journal_id": batch.last_journal_id,
        "entry_count": batch.entry_count,
        "merkle_root": batch.merkle_root,
        "previous_batch_hash": batch.previous_batch_hash,
        "sealed_at": batch.sealed_at.isoformat(),
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return signer.verify(manifest_bytes, batch.signature)
