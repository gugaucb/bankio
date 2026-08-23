"""Anchor commitment contract: cryptographic material only."""
import pytest
from apps.ledger import anchors


def test_commitment_is_deterministic():
    class FakeBatch:
        sequence = 3
        merkle_root = "a" * 64
        batch_manifest_hash = "b" * 64

    c1 = anchors.anchor_commitment(FakeBatch())
    c2 = anchors.anchor_commitment(FakeBatch())
    assert c1["commitment"] == c2["commitment"]


FORBIDDEN_SUBSTRINGS = ["customer", "account_number", "balance", "amount", "name", "kyc", "description"]


def test_commitment_contains_no_pii_fields():
    class FakeBatch:
        sequence = 1
        merkle_root = "c" * 64
        batch_manifest_hash = "d" * 64

    payload = anchors.anchor_commitment(FakeBatch())
    text = " ".join(payload).lower()
    assert not any(word in text for word in FORBIDDEN_SUBSTRINGS)


def test_idempotency_key_binds_version_sequence_and_manifest():
    class FakeBatch:
        sequence = 7
        merkle_root = "e" * 64
        batch_manifest_hash = "f" * 64

    key = anchors.anchor_idempotency_key(FakeBatch())
    assert key == "anchor-v1:7:" + "f" * 64
