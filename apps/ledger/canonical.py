"""Canonical serialization and hash chaining for posted journals.

Cryptographic material only — this layer NEVER alters accounting truth.
Every object records its algorithm and canonicalization version so future
schema evolution cannot silently invalidate old proofs.

Domain separation prevents hash reuse across contexts.
"""
import hashlib
import json
from decimal import Decimal

CANONICALIZATION_VERSION = "ledger-c14n-v1"
HASH_ALGORITHM = "SHA-256"
PROOF_VERSION = "proof-v1"
DOMAIN_ENTRY = "BANKIO:LEDGER:ENTRY:V1"
DOMAIN_CHAIN = "BANKIO:LEDGER:CHAIN:V1"

GENESIS_HASH = "0" * 64


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.01")))
    raise TypeError(f"Unserializable {type(value)}")


def canonical_payload(journal) -> dict:
    """Deterministic canonical representation of a POSTED journal."""
    postings = sorted(
        (
            {
                "account_code": e.account.code,
                "side": e.side,
                "amount": str(e.amount.quantize(Decimal("0.01"))),
            }
            for e in journal.entries.select_related("account")
        ),
        key=lambda p: (p["account_code"], p["side"], p["amount"]),
    )
    return {
        "schema_version": CANONICALIZATION_VERSION,
        "journal_id": journal.pk,
        "reference": journal.reference,
        "transaction_type": "JOURNAL",
        "effective_at": journal.posted_at.isoformat() if journal.posted_at else None,
        "currency": journal.currency,
        "postings": postings,
    }


def canonical_bytes(journal) -> bytes:
    payload = canonical_payload(journal)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default).encode()


def payload_hash(journal) -> str:
    data = DOMAIN_ENTRY.encode() + b"|" + canonical_bytes(journal)
    return hashlib.sha256(data).hexdigest()


def chain_hash(previous_chain_hash: str, entry_payload_hash: str) -> str:
    data = "|".join([DOMAIN_CHAIN, previous_chain_hash, entry_payload_hash])
    return hashlib.sha256(data.encode()).hexdigest()
