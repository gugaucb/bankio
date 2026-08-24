"""Out-of-band delivery of step-up challenge codes (spec PART 10 closing).

Reuses issue_challenge()/RiskChallenge unchanged. This module ONLY owns:

  1. delivery — the plaintext code goes to the simulated out-of-band
     channel (`bankio.challenge` logger stands in for an SMS/e-mail
     gateway in development). It must NEVER appear in HTML, query
     strings, AuditLog or be persisted in plaintext.
  2. issuance audit trail — CHALLENGE_ISSUED carries identifiers only.
  3. an in-app Notification telling the customer a code was sent
     (content includes no code).

To integrate a real provider, swap deliver(); callers never touch codes.
"""
import logging

from apps.audit.services import record as audit
from apps.notifications.services import notify

from .challenge import issue_challenge

logger = logging.getLogger("bankio.challenge")


def deliver(customer, challenge, code):
    """Simulated out-of-band transport. Development stand-in for SMS/email."""
    logger.info(
        "[step-up] challenge CHL-%s code for %s: %s (valid 10 minutes)",
        challenge.pk, customer.username, code,
    )


def issue_and_deliver(evaluation, customer, material_facts, actor=None, request=None):
    """Issue a bound challenge, deliver its code out-of-band, audit issuance."""
    challenge, code = issue_challenge(evaluation, customer, material_facts)
    deliver(customer, challenge, code)
    audit(
        actor=actor, action="CHALLENGE_ISSUED", request=request, resource=challenge,
        metadata={
            "operation_type": evaluation.operation_type,
            "evaluation_id": evaluation.pk,
            "expires_at": challenge.expires_at.isoformat(),
        },
    )
    notify(
        recipient=customer, category="SECURITY", kind="CHALLENGE_ISSUED",
        title="Verification code sent",
        body=(f"A verification code was sent to you to confirm a "
              f"{evaluation.operation_type.replace('_', ' ').lower()} operation. "
              f"It expires in 10 minutes."),
        metadata={"operation": evaluation.operation_type},
        dedup_key=f"CHALLENGE_ISSUED:{challenge.pk}:{customer.pk}",
    )
    return challenge, code
