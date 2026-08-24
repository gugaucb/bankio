"""Concurrency-safe challenge confirmation + hardening (spec PART 10 closing).

The core services (verify_challenge/consume_challenge) stay untouched;
this module adds the protection layer around them:

  - row lock around verify→consume: two simultaneous requests can never
    both pass the PENDING check (1 challenge → ≤1 settlement);
  - brute-force limit: MAX_ATTEMPTS wrong codes tombstone the challenge
    (failure evidence is recorded outside the locking transaction so it
    survives its own rollback);
  - reissue with cooldown: a fresh code for an unanswered challenge is a
    NEW challenge (old one superseded); spam is rate-limited.

Audit trail: CHALLENGE_VERIFIED / CONSUMED / FAILED / EXPIRED / REISSUED —
identifiers only, never codes or hashes.
"""
import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record as audit
from apps.notifications.models import Notification

from .challenge import (
    CHALLENGE_TTL_MINUTES,
    ChallengeError,
    _facts_digest,
    consume_challenge,
    verify_challenge,
)
from .models import RiskChallenge

MAX_ATTEMPTS = 5
REISSUE_COOLDOWN_SECONDS = 60


def _generate_code():
    return f"{secrets.randbelow(1000000):06d}"


def _failed_attempts(challenge_id):
    from apps.audit.models import AuditLog

    return AuditLog.objects.filter(
        action="CHALLENGE_FAILED", resource_id=str(challenge_id)).count()


def _record_failure(challenge):
    """One wrong-code event. At MAX_ATTEMPTS the challenge is tombstoned."""
    attempts = _failed_attempts(challenge.pk) + 1
    audit(actor=challenge.customer, action="CHALLENGE_FAILED", resource=challenge,
          metadata={"operation_type": challenge.evaluation.operation_type,
                    "attempts": attempts})
    if attempts >= MAX_ATTEMPTS:
        RiskChallenge.objects.filter(pk=challenge.pk).update(
            status=RiskChallenge.Status.EXPIRED)
        audit(actor=challenge.customer, action="CHALLENGE_EXPIRED", resource=challenge,
              metadata={"reason": "MAX_ATTEMPTS"})


def _precheck_material(challenge, material_facts):
    """Tombstone on material tampering BEFORE the locking transaction so the
    EXPIRED state survives confirm_and_consume's rollback. Only meaningful
    while PENDING: consume_challenge() deliberately rewrites material_hash
    after use (anti-replay), which must not read as tampering."""
    if challenge.status == RiskChallenge.Status.PENDING and \
            _facts_digest(material_facts) != challenge.material_hash:
        RiskChallenge.objects.filter(pk=challenge.pk).update(
            status=RiskChallenge.Status.EXPIRED)
        raise ChallengeError("MATERIAL_CHANGED")


# ------------------------------------------------------------- confirmation

@transaction.atomic
def confirm_and_consume(challenge_id, customer, code, material_facts,
                        operation_reference, actor=None, request=None):
    """Atomically verify code+material binding and consume the challenge."""
    challenge = (
        RiskChallenge.objects.select_for_update()
        .select_related("evaluation")
        .filter(pk=challenge_id).first()
    )
    if challenge is None or customer is None or challenge.customer_id != customer.id:
        raise ChallengeError("CHALLENGE_NOT_FOUND")
    verified = verify_challenge(challenge, code, material_facts)
    audit(actor=actor or customer, action="CHALLENGE_VERIFIED", request=request,
          resource=verified,
          metadata={"operation_type": verified.evaluation.operation_type})
    consume_challenge(verified, operation_reference)
    audit(actor=actor or customer, action="CHALLENGE_CONSUMED", request=request,
          resource=verified,
          metadata={"operation_reference": operation_reference})
    return verified


def confirm(challenge_id, customer, code, material_facts,
            operation_reference, actor=None, request=None):
    """confirm_and_consume plus hardening: material mismatch tombstones before
    the locking transaction (so the EXPIRED state survives its own rollback),
    and wrong codes feed the brute-force counter."""
    probe = RiskChallenge.objects.select_related("evaluation").filter(
        pk=challenge_id, customer_id=getattr(customer, "id", None)).first()
    if probe is None or customer is None:
        raise ChallengeError("CHALLENGE_NOT_FOUND")
    _precheck_material(probe, material_facts)
    try:
        return confirm_and_consume(challenge_id, customer, code, material_facts,
                                   operation_reference, actor=actor, request=request)
    except ChallengeError as exc:
        if str(exc) == "INVALID_CODE":
            _record_failure(probe)   # outside the rolled-back transaction
        raise


def attempt(challenge_id, customer, code, material_facts, actor=None, request=None):
    """Verify-only path (standalone challenge page). Brute-force guarded."""
    probe = RiskChallenge.objects.select_related("evaluation").filter(pk=challenge_id).first()
    if probe is None or customer is None or probe.customer_id != customer.id:
        raise ChallengeError("CHALLENGE_NOT_FOUND")
    _precheck_material(probe, material_facts)
    try:
        with transaction.atomic():
            locked = RiskChallenge.objects.select_for_update().get(pk=probe.pk)
            verified = verify_challenge(locked, code, material_facts)
    except ChallengeError as exc:
        if str(exc) == "INVALID_CODE":
            _record_failure(probe)   # outside the rolled-back transaction
        raise
    audit(actor=actor or customer, action="CHALLENGE_VERIFIED", request=request,
          resource=verified,
          metadata={"operation_type": verified.evaluation.operation_type})
    return verified


# ------------------------------------------------------------------ reissue

def reissue_challenge(challenge_id, customer, actor=None, request=None):
    """Fresh code for an UNANSWERED challenge: a brand-new RiskChallenge with
    the same evaluation and material binding; the old one is superseded
    (EXPIRED, its code dead). Rate-limited per customer by cooldown."""
    old = RiskChallenge.objects.select_related("evaluation").filter(
        pk=challenge_id, customer_id=getattr(customer, "id", None)).first()
    if old is None or customer is None:
        raise ChallengeError("CHALLENGE_NOT_FOUND")
    if old.status != RiskChallenge.Status.PENDING:
        raise ChallengeError("CHALLENGE_NOT_PENDING")

    since = timezone.now() - timedelta(seconds=REISSUE_COOLDOWN_SECONDS)
    if RiskChallenge.objects.filter(
            customer=customer, created_at__gte=since).exclude(pk=old.pk).exists():
        raise ChallengeError("REISSUE_COOLDOWN")

    code = _generate_code()
    new = RiskChallenge.objects.create(
        customer=customer, evaluation=old.evaluation,
        material_hash=old.material_hash,
        code_hash=hashlib.sha256(code.encode()).hexdigest()[:32],
        expires_at=timezone.now() + timedelta(minutes=CHALLENGE_TTL_MINUTES),
    )
    RiskChallenge.objects.filter(pk=old.pk).update(status=RiskChallenge.Status.EXPIRED)
    audit(actor=actor or customer, action="CHALLENGE_REISSUED", request=request,
          resource=new,
          metadata={"operation_type": old.evaluation.operation_type,
                    "previous_challenge": old.pk})

    from .challenge_delivery import deliver

    deliver(customer, new, code)   # test/dev channel asserts against this
    # re-derive hash consistency: deliver() received the plaintext we hashed
    Notification.objects.create(
        recipient=customer, category="SECURITY",
        title="Verification code sent",
        body=(f"A new verification code was sent to you for a "
              f"{old.evaluation.operation_type.replace('_', ' ').lower()} "
              f"operation. The previous code is no longer valid."),
    )
    return new, code
