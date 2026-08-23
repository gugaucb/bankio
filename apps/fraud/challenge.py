"""Step-up challenge workflow (spec PART 10).

CHALLENGE is a real server-side workflow: a code is issued against a
material hash of the operation facts. Verification requires the correct
code AND unchanged material; success consumes the challenge. A hidden
frontend field can never substitute for this.
"""
import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from .models import RiskChallenge, RiskEvaluation

CHALLENGE_TTL_MINUTES = 10


class ChallengeError(ValueError):
    pass


def material_hash(*facts) -> str:
    joined = "|".join(str(f) for f in facts)
    return hashlib.sha256(joined.encode()).hexdigest()


def _facts_digest(material_facts) -> str:
    """Deterministic digest over fact VALUES (sorted keys), not just names."""
    if isinstance(material_facts, dict):
        return material_hash(*[f"{k}={material_facts[k]}" for k in sorted(material_facts)])
    return material_hash(*material_facts)


def issue_challenge(evaluation: RiskEvaluation, customer, material_facts):
    """Create a pending challenge bound to the operation's material facts."""
    code = f"{secrets.randbelow(1000000):06d}"  # production: deliver via SMS/email
    ch = RiskChallenge.objects.create(
        customer=customer,
        evaluation=evaluation,
        material_hash=_facts_digest(material_facts),
        code_hash=hashlib.sha256(code.encode()).hexdigest()[:32],
        expires_at=timezone.now() + timedelta(minutes=CHALLENGE_TTL_MINUTES),
    )
    return ch, code


def verify_challenge(challenge: RiskChallenge, code, material_facts):
    """Verify code + material binding; consume on success."""
    _expire_if_due(challenge)
    if challenge.status == RiskChallenge.Status.EXPIRED:
        raise ChallengeError("CHALLENGE_EXPIRED")
    if challenge.status != RiskChallenge.Status.PENDING:
        raise ChallengeError("CHALLENGE_NOT_PENDING")  # includes replay attempts
    if _facts_digest(material_facts) != challenge.material_hash:
        # material changed after issuance — challenge is dead (INV 5)
        challenge.status = RiskChallenge.Status.EXPIRED
        challenge.save(update_fields=["status"])
        raise ChallengeError("MATERIAL_CHANGED")
    if hashlib.sha256((code or "").encode()).hexdigest()[:32] != challenge.code_hash:
        raise ChallengeError("INVALID_CODE")
    challenge.status = RiskChallenge.Status.VERIFIED
    challenge.verified_at = timezone.now()
    challenge.save(update_fields=["status", "verified_at"])
    return challenge


def consume_challenge(challenge: RiskChallenge, operation_reference: str):
    """Bind the verified challenge to the exact operation being settled."""
    if challenge.status != RiskChallenge.Status.VERIFIED:
        raise ChallengeError("CHALLENGE_NOT_VERIFIED")
    challenge.material_hash = material_hash("consumed", operation_reference)
    challenge.status = RiskChallenge.Status.CONSUMED
    challenge.save(update_fields=["status", "material_hash"])


def _expire_if_due(challenge):
    if (
        challenge.status == RiskChallenge.Status.PENDING
        and timezone.now() > challenge.expires_at
    ):
        challenge.status = RiskChallenge.Status.EXPIRED
        challenge.save(update_fields=["status"])
