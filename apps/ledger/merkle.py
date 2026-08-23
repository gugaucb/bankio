"""Deterministic Merkle tree over journal payload hashes.

Leaves are the journals' payload_hash values (already domain-separated).
Odd nodes duplicate the last node (Bitcoin-style). All rules are fixed by
MERKLE_VERSION so proofs stay verifiable as the system evolves.
"""
import hashlib

MERKLE_VERSION = "merkle-v1"
DOMAIN_LEAF = "BANKIO:LEDGER:MERKLE:LEAF:V1"
DOMAIN_NODE = "BANKIO:LEDGER:MERKLE:NODE:V1"


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def leaf_hash(payload_hash: str) -> str:
    return _h(DOMAIN_LEAF.encode() + b"|" + payload_hash.encode())


def _node_hash(left: str, right: str) -> str:
    return _h(DOMAIN_NODE.encode() + b"|" + left.encode() + b"|" + right.encode())


def build_levels(leaf_hashes):
    """Return all levels, leaves first. Deterministic for a given input order."""
    if not leaf_hashes:
        raise ValueError("Merkle tree requires at least one leaf")
    level = [leaf_hash(l) for l in leaf_hashes]
    levels = [level]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level = level + [level[-1]]
            levels[-1] = level
        nxt = [_node_hash(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        levels.append(nxt)
        level = nxt
    return levels


def merkle_root(leaf_hashes) -> str:
    return build_levels(leaf_hashes)[-1][0]


def generate_proof(leaf_hashes, index: int) -> list:
    """Authentication path for leaf at index: list of {hash, side}."""
    levels = build_levels(leaf_hashes)
    proof = []
    idx = index
    for level in levels[:-1]:
        sibling = idx ^ 1
        if sibling >= len(level):  # duplicated last node
            sibling = len(level) - 1
        proof.append({
            "hash": level[sibling],
            "side": "LEFT" if sibling < idx else "RIGHT",
        })
        idx //= 2
    return proof


def verify_proof(leaf_payload_hash: str, proof, root: str) -> bool:
    current = leaf_hash(leaf_payload_hash)
    for step in proof:
        if step["side"] == "LEFT":
            current = _node_hash(step["hash"], current)
        else:
            current = _node_hash(current, step["hash"])
    return current == root
