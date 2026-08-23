"""End-to-end historical proof verification for auditors.

Chain: canonical hash -> hash chain -> Merkle proof -> sealed root ->
signed batch manifest -> external anchor confirmation.
"""
from django.conf import settings

from . import canonical, merkle
from .models import LedgerAnchor, JournalEntry, LedgerProofBatch
from .anchors import anchor_commitment, anchor_idempotency_key
from .proof_batches import generate_merkle_proof, verify_batch_signature

PENDING = "PENDING"
VERIFIED = "VERIFIED"
FAILED = "FAILED"


def verify_journal(journal_ref: str) -> dict:
    """Return a step-by-step verification report for one journal."""
    report = {"journal": journal_ref, "steps": [], "result": FAILED}
    ok = True

    def step(name, passed, detail=""):
        nonlocal ok
        report["steps"].append({"step": name, "status": "PASS" if passed else "FAIL", "detail": detail})
        if not passed:
            ok = False

    j = JournalEntry.objects.filter(reference=journal_ref).first()
    step("journal_exists", j is not None)
    if not j:
        return report
    step("posted", j.status == "POSTED")

    # 1. canonical hash recomputes
    ph = canonical.payload_hash(j)
    step("canonical_hash", j.payload_hash == ph,
         f"expected={ph} actual={j.payload_hash}")
    if not ok:
        return report

    # 2. hash-chain linkage (previous link + stored chain hash)
    expected_chain = canonical.chain_hash(j.previous_entry_hash or "", j.payload_hash)
    step("hash_chain", j.chain_hash == expected_chain and bool(j.previous_entry_hash))
    if not ok:
        return report

    # 3. merkle inclusion proof against a sealed batch
    try:
        batch, proof = generate_merkle_proof(j)
    except Exception:
        step("merkle_proof", False, "no sealed batch contains this journal")
        return report
    step("merkle_proof",
         merkle.verify_proof(j.payload_hash, proof, batch.merkle_root),
         f"batch #{batch.sequence} root={batch.merkle_root[:16]}...")
    if not ok:
        return report

    # 4. signed manifest
    step("batch_signature", verify_batch_signature(batch),
         f"key={batch.signature.get('key_id') if batch.signature else None}")

    # 5. external anchor
    key = anchor_idempotency_key(batch)
    anchor = (
        LedgerAnchor.objects.filter(idempotency_key=key)
        .exclude(status=LedgerAnchor.Status.SUPERSEDED).first()
    )
    if not anchor:
        report["result"] = PENDING
        step("external_anchor", False, "not anchored yet")
        return report
    step("anchor_exists", True, f"{anchor.provider}:{anchor.anchor_reference}")

    confirmed = anchor.status == LedgerAnchor.Status.CONFIRMED and anchor.confirmed_at is not None
    step("anchor_confirmation", confirmed,
         f"status={anchor.status}" + (f" at {anchor.confirmed_at.isoformat()}" if confirmed else ""))
    if not confirmed:
        report["result"] = PENDING
        return report

    step("anchor_commitment_matches",
         anchor.commitment == anchor_commitment(batch)["commitment"])

    report["result"] = VERIFIED if ok else FAILED
    return report


def pretty(report: dict) -> str:
    lines = [f"Journal: {report['journal']}"]
    for s in report["steps"]:
        lines.append(f"{s['step']}: {s['status']}" + (f" ({s['detail']})" if s['detail'] else ""))
    final = {
        VERIFIED: "FINAL RESULT:\nCRYPTOGRAPHICALLY VERIFIED",
        PENDING: "FINAL RESULT:\nPENDING VERIFICATION",
        FAILED: "FINAL RESULT:\nPROOF INVALID",
    }[report["result"]]
    lines.append(final)
    return "\n".join(lines)
