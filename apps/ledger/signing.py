"""Ledger proof signing boundary.

The ONLY place in the project that touches signing keys. Production should
replace DevEd25519Signer with an HSM/KMS-backed implementation of
LedgerProofSigner without any change to ledger or proof logic.

Every signature records key_id, algorithm and signature_version for rotation.
Private keys NEVER touch the database, logs or templates.
"""
from abc import ABC, abstractmethod

SIGNATURE_VERSION = "sig-v1"


class LedgerProofSigner(ABC):
    @abstractmethod
    def sign(self, payload: bytes) -> dict:
        """Return {algorithm, key_id, signature_version, signature}."""

    @abstractmethod
    def verify(self, payload: bytes, signature: dict) -> bool:
        ...


class DevEd25519Signer(LedgerProofSigner):
    """Local development signer. NOT for production use."""

    ALGORITHM = "ED25519"

    def __init__(self, private_key=None, key_id="bankio-dev-key-1"):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self._key = private_key or Ed25519PrivateKey.generate()
        self.key_id = key_id

    def public_key(self):
        return self._key.public_key()

    def sign(self, payload: bytes) -> dict:
        raw = self._key.sign(payload)
        import base64

        return {
            "algorithm": self.ALGORITHM,
            "key_id": self.key_id,
            "signature_version": SIGNATURE_VERSION,
            "signature": base64.b64encode(raw).decode(),
        }

    def verify(self, payload: bytes, signature: dict) -> bool:
        import base64

        from cryptography.exceptions import InvalidSignature

        if signature.get("algorithm") != self.ALGORITHM:
            return False
        if signature.get("key_id") != self.key_id:
            return False
        if signature.get("signature_version") != SIGNATURE_VERSION:
            return False
        try:
            self._key.public_key().verify(
                base64.b64decode(signature["signature"]), payload
            )
            return True
        except (InvalidSignature, KeyError, ValueError):
            return False


_default_signer = None


def default_signer() -> LedgerProofSigner:
    """Process-wide development signer (swap point for KMS/HSM)."""
    global _default_signer
    if _default_signer is None:
        _default_signer = DevEd25519Signer()
    return _default_signer
