"""Simulated blockchain anchoring: full lifecycle without real chain fees."""
import threading

from apps.audit.services import record as audit

from .anchors import AnchorProviderError, BlockchainAnchorProvider
from .models import LedgerAnchor, LedgerProofBatch


class SimulatedBlockchainAnchorProvider(BlockchainAnchorProvider):
    """Deterministic in-memory chain simulator.

    Status advances on each poll: SUBMITTED -> CONFIRMING -> CONFIRMED.
    Set fail_next=True to simulate submission failure.
    """

    def __init__(self):
        self._store = {}
        self._polls = {}
        self._fail_next = False
        self._lock = threading.Lock()

    def fail_next(self):
        self._fail_next = True

    def submit(self, commitment: dict, idempotency_key: str) -> str:
        if self._fail_next:
            self._fail_next = False
            raise AnchorProviderError("simulated provider unavailable")
        # idempotency at the provider boundary too
        for ref, entry in self._store.items():
            if entry["idempotency_key"] == idempotency_key:
                return ref
        import hashlib
        import json

        digest = hashlib.sha256(json.dumps(commitment, sort_keys=True).encode()).hexdigest()
        ref = f"SIM-{digest[:16].upper()}"
        with self._lock:
            self._store[ref] = {
                "commitment": commitment["commitment"],
                "idempotency_key": idempotency_key,
                "status": "SUBMITTED",
            }
            self._polls[ref] = 0
        return ref

    def get_status(self, anchor_reference: str) -> str:
        with self._lock:
            entry = self._store[anchor_reference]
            self._polls[anchor_reference] += 1
            if entry["status"] in ("SUBMITTED",) and self._polls[anchor_reference] >= 2:
                entry["status"] = "CONFIRMED"  # simulated block mined
            return entry["status"]

    def verify(self, anchor_reference: str, commitment: dict) -> bool:
        entry = self._store.get(anchor_reference)
        return bool(entry and entry["commitment"] == commitment["commitment"])

    def is_confirmed(self, anchor_reference: str) -> bool:
        return self.get_status(anchor_reference) == "CONFIRMED"


_default_provider = None


def default_provider() -> SimulatedBlockchainAnchorProvider:
    global _default_provider
    if _default_provider is None:
        _default_provider = SimulatedBlockchainAnchorProvider()
    return _default_provider


# ----------------------------------------------------------------- service


class AnchorError(Exception):
    pass


def anchor_batch(batch: LedgerProofBatch, provider=None) -> LedgerAnchor:
    """Submit one sealed batch. Idempotent by derived key; ledger never
    depends on the anchor succeeding."""
    from .anchors import anchor_commitment, anchor_idempotency_key

    provider = provider or default_provider()
    key = anchor_idempotency_key(batch)
    existing = (
        LedgerAnchor.objects.filter(idempotency_key=key)
        .exclude(status=LedgerAnchor.Status.SUPERSEDED)
        .first()
    )
    if existing:
        if existing.status == LedgerAnchor.Status.FAILED:
            # failed attempts are superseded; retry creates a fresh submission
            existing.status = LedgerAnchor.Status.SUPERSEDED
            existing.save(update_fields=["status"])
        else:
            return existing

    commitment = anchor_commitment(batch)
    anchor = LedgerAnchor.objects.create(
        batch=batch,
        provider=provider.__class__.__name__,
        idempotency_key=key,
        commitment=commitment["commitment"],
        status=LedgerAnchor.Status.CREATED,
    )
    try:
        ref = provider.submit(commitment, key)
    except AnchorProviderError as exc:
        anchor.status = LedgerAnchor.Status.FAILED
        anchor.error = str(exc)
        anchor.save(update_fields=["status", "error"])
        audit(action="BLOCKCHAIN_ANCHOR_FAILED", resource=batch,
              metadata={"error": str(exc)})
        return anchor
    anchor.anchor_reference = ref
    anchor.status = LedgerAnchor.Status.SUBMITTED
    anchor.save(update_fields=["anchor_reference", "status"])
    audit(action="BLOCKCHAIN_ANCHOR_SUBMITTED", resource=batch,
          metadata={"reference": ref})
    return anchor


def confirm_anchor(anchor: LedgerAnchor, provider=None) -> LedgerAnchor:
    """Poll a submitted anchor; marks the batch ANCHORED once confirmed."""
    from .anchors import anchor_commitment

    provider = provider or default_provider()
    if anchor.status not in (LedgerAnchor.Status.SUBMITTED, LedgerAnchor.Status.CONFIRMING):
        return anchor
    anchor.status = provider.get_status(anchor.anchor_reference)
    if anchor.status == LedgerAnchor.Status.CONFIRMED:
        if not provider.verify(anchor.anchor_reference, anchor_commitment(anchor.batch)):
            anchor.status = LedgerAnchor.Status.FAILED
            anchor.error = "commitment mismatch on verification"
        else:
            anchor.confirmed_at = timezone_now()
            LedgerProofBatch.objects.filter(pk=anchor.batch_id).update(
                status=LedgerProofBatch.Status.ANCHORED
            )
            audit(action="BLOCKCHAIN_ANCHOR_CONFIRMED", resource=anchor.batch,
                  metadata={"reference": anchor.anchor_reference})
    anchor.save(update_fields=["status", "error", "confirmed_at"])
    return anchor


def timezone_now():
    from django.utils import timezone

    return timezone.now()
