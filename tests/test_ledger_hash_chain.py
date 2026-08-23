"""Canonical hashing and hash chain: deterministic, tamper-evident."""
import hashlib
from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.ledger import canonical, services as ledger
from apps.ledger.models import JournalEntry


@pytest.fixture
def accounts(db):
    cash = ledger.get_or_create_account("HC-CASH", "Cash", type="ASSET")
    rev = ledger.get_or_create_account("HC-REV", "Revenue", type="INCOME")
    return cash, rev


def _post(cash, rev, ref, amount="10.00"):
    return ledger.post_journal(ref, "x", [(cash, "DEBIT", amount), (rev, "CREDIT", amount)])


def test_canonical_serialization_is_deterministic(accounts):
    cash, rev = accounts
    a = _post(cash, rev, "HC-DET-A", "12.34")
    b1 = canonical.canonical_bytes(a)
    b2 = canonical.canonical_bytes(JournalEntry.objects.get(pk=a.pk))
    assert b1 == b2
    # same data -> same hash (PROOF INVARIANT 1)
    assert canonical.payload_hash(a) == canonical.payload_hash(a)


def test_domain_separation_changes_digest(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "HC-DOM")
    h_dom = hashlib.sha256(
        canonical.DOMAIN_ENTRY.encode() + b"|" + canonical.canonical_bytes(j)
    ).hexdigest()
    h_plain = hashlib.sha256(canonical.canonical_bytes(j)).hexdigest()
    assert h_dom != h_plain


def test_chain_links_genesis_then_extends(accounts):
    cash, rev = accounts
    a = _post(cash, rev, "HC-1")
    b = _post(cash, rev, "HC-2")
    assert a.previous_entry_hash == canonical.GENESIS_HASH
    assert b.previous_entry_hash == a.chain_hash
    assert b.chain_hash == canonical.chain_hash(a.chain_hash, b.payload_hash)


def test_changing_data_changes_commitment(accounts):
    """PROOF INVARIANT 2: different amounts -> different payload hashes."""
    cash, rev = accounts
    a = _post(cash, rev, "HC-V1", "10.00")
    b = _post(cash, rev, "HC-V2", "20.00")
    assert a.payload_hash != b.payload_hash


def test_verify_command_detects_tampered_journal(accounts):
    """A→B→C chain; corrupt B's stored hash -> verification fails at B."""
    cash, rev = accounts
    for i, ref in enumerate(["HC-T1", "HC-T2", "HC-T3"]):
        _post(cash, rev, ref, f"{10 + i}.00")

    out = __import__("io").StringIO()
    call_command("verify_ledger_hash_chain", stdout=out)
    assert "HASH CHAIN VALID" in out.getvalue()

    # tamper with the middle journal's payload hash (simulating DB edit)
    victim = JournalEntry.objects.get(reference="HC-T2")
    JournalEntry.objects.filter(pk=victim.pk).update(payload_hash="f" * 64)

    out = __import__("io").StringIO()
    try:
        call_command("verify_ledger_hash_chain", stdout=out)
    except SystemExit:
        pass
    text = out.getvalue()
    assert "INVALID" in text
    assert "HC-T2" in text


def test_verify_command_detects_deleted_link(accounts):
    cash, rev = accounts
    _post(cash, rev, "HC-L1")
    mid = _post(cash, rev, "HC-L2")
    tail = _post(cash, rev, "HC-L3")
    # break linkage of L3 (pretend L2 was rewritten)
    JournalEntry.objects.filter(pk=tail.pk).update(previous_entry_hash="e" * 64)
    out = __import__("io").StringIO()
    try:
        call_command("verify_ledger_hash_chain", stdout=out)
    except SystemExit:
        pass
    assert "INVALID" in out.getvalue()


def test_reversals_extend_the_chain(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "HC-R1")
    r = ledger.reverse_journal(j)
    assert r.chain_hash is not None
    assert r.previous_entry_hash == j.chain_hash
