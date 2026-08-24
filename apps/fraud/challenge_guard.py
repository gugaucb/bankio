"""Concurrency-safe challenge confirmation (spec PART 10 closing).

The core services (verify_challenge/consume_challenge) stay untouched;
this wrapper adds a row lock around verify→consume so two simultaneous
requests can never both pass the PENDING check:

    1 challenge  →  at most 1 consumption  →  at most 1 settlement

Hardening (attempt limits/cooldown/reissue) also lives here so all
challenge-protection policy has a single home.
"""
from django.db import transaction

from apps.audit.services import record as audit

from .challenge import (
    ChallengeError,
    _facts_digest,
    consume_challenge,
    verify_challenge,
)
from .models import RiskChallenge


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
    audit(actor=actor, action="CHALLENGE_VERIFIED", request=request,
          resource=verified,
          metadata={"operation_type": verified.evaluation.operation_type})
    consume_challenge(verified, operation_reference)
    audit(actor=actor, action="CHALLENGE_CONSUMED", request=request,
          resource=verified,
          metadata={"operation_reference": operation_reference})
    return verified


def confirm(challenge_id, customer, code, material_facts,
            operation_reference, actor=None, request=None):
    """confirm_and_consume plus one safety: a MATERIAL_CHANGED rejection must
    survive its own rollback, so the material binding is checked (and the
    challenge tombstoned as EXPIRED) before entering the locking transaction."""
    probe = RiskChallenge.objects.filter(
        pk=challenge_id, customer_id=getattr(customer, "id", None)).first()
    if probe is None or customer is None:
        raise ChallengeError("CHALLENGE_NOT_FOUND")
    if _facts_digest(material_facts) != probe.material_hash:
        RiskChallenge.objects.filter(pk=probe.pk).update(
            status=RiskChallenge.Status.EXPIRED)
        raise ChallengeError("MATERIAL_CHANGED")
    return confirm_and_consume(challenge_id, customer, code, material_facts,
                               operation_reference, actor=actor, request=request)
