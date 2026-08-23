"""Provider-neutral external anchoring.

Anchors only cryptographic commitments — NEVER customer data (names,
account numbers, balances, amounts, descriptions, KYC).

Blockchain proves that a commitment existed at or before a point in time.
It is NOT a transaction processor, balance store or backup.
"""
from abc import ABC, abstractmethod

ANCHOR_CONTENT_VERSION = "anchor-v1"
DOMAIN_ANCHOR = "BANKIO:ANCHOR:COMMITMENT:V1"


class AnchorProviderError(Exception):
    pass


def anchor_commitment(batch) -> dict:
    """The exact commitment published externally for a sealed batch."""
    import hashlib
    import json

    payload = {
        "content_version": ANCHOR_CONTENT_VERSION,
        "system": "BANKIO",
        "proof_version": "proof-v1",
        "batch_sequence": batch.sequence,
        "merkle_root": batch.merkle_root,
        "batch_manifest_hash": batch.batch_manifest_hash,
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {"commitment": hashlib.sha256(DOMAIN_ANCHOR.encode() + b"|" + data).hexdigest(), **payload}


def anchor_idempotency_key(batch) -> str:
    """Same batch must never produce uncontrolled duplicate logical anchors."""
    return f"anchor-v1:{batch.sequence}:{batch.batch_manifest_hash}"


class BlockchainAnchorProvider(ABC):
    """Swap-point: simulated provider in dev/tests, real chain adapter later."""

    @abstractmethod
    def submit(self, commitment: dict, idempotency_key: str) -> str:
        """Submit a commitment; returns an anchor reference."""

    @abstractmethod
    def get_status(self, anchor_reference: str) -> str:
        """One of CREATED/SUBMITTED/CONFIRMING/CONFIRMED/FAILED."""

    @abstractmethod
    def verify(self, anchor_reference: str, commitment: dict) -> bool:
        """True when the on-chain record exactly matches the commitment."""
