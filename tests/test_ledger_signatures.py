"""Digital signature boundary: authenticity of proof batches."""
import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.ledger.signing import DevEd25519Signer, SIGNATURE_VERSION


@pytest.fixture
def signer():
    return DevEd25519Signer(key_id="test-key-1")


def test_sign_and_verify_roundtrip(signer):
    payload = b"BANKIO:PROOF:BATCH:v1|root=abc123"
    sig = signer.sign(payload)
    assert sig["key_id"] == "test-key-1"
    assert sig["signature_version"] == SIGNATURE_VERSION
    assert signer.verify(payload, sig) is True


def test_modified_payload_fails_verification(signer):
    payload = b"original"
    sig = signer.sign(payload)
    assert signer.verify(b"tampered", sig) is False


def test_wrong_key_fails_verification():
    s1 = DevEd25519Signer(Ed25519PrivateKey.generate(), key_id="k1")
    s2 = DevEd25519Signer(Ed25519PrivateKey.generate(), key_id="k2")
    sig = s1.sign(b"data")
    # right key id but wrong key material
    s2_wrong_id = DevEd25519Signer(s2._key, key_id="k1")
    assert s2_wrong_id.verify(b"data", sig) is False


def test_garbage_signature_rejected(signer):
    sig = signer.sign(b"data")
    bad = dict(sig, signature=base64.b64encode(b"junkjunkjunk").decode())
    assert signer.verify(b"data", bad) is False
    assert signer.verify(b"data", {"algorithm": "RSA", "signature": "x"}) is False


def test_deterministic_key_reuse_same_signature(signer):
    p = b"same"
    s1 = signer.sign(p)
    s2 = signer.sign(p)
    assert s1 == s2  # Ed25519 is deterministic
