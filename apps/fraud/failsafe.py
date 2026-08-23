"""Fail-safe policy matrix (spec PART 31, INV 9/10).

Explicit, versioned answer to "what happens when the risk engine itself
fails?" — never an implicit default. Strategies:

  FAIL_OPEN   — operation proceeds; failure is recorded (audit + FAILED
                evaluation snapshot). Used for money movement while the
                engine is observational; a blocked engine must not freeze
                the bank.
  FAIL_CLOSED — operation must not proceed. Reserved for authentication
                and other operations where denying is the safe direction.

Unknown operations resolve to FAIL_CLOSED: the safe direction is the
default for anything the matrix has not explicitly considered.
"""
FAILSAFE_VERSION = "failsafe-v1"

FAIL_OPEN = "FAIL_OPEN"
FAIL_CLOSED = "FAIL_CLOSED"

# money movement / lifecycle ops: availability first, evidence always kept
_FAIL_OPEN_OPS = {
    "TRANSFER",
    "CARD_PURCHASE",
    "BILL_PAYMENT",
    "ACCOUNT_OPENING",
}

# identity / access ops: deny is the safe direction
_FAIL_CLOSED_OPS = {
    "LOGIN",
}


def resolve_failure(operation_type):
    """Return the fail-safe strategy for an operation type. Unknown → FAIL_CLOSED."""
    if operation_type in _FAIL_OPEN_OPS:
        return FAIL_OPEN
    return FAIL_CLOSED


def record_failure(operation_type, exc, actor=None):
    """Persist the failure evidence shared by both strategies:
    audit event + FAILED evaluation snapshot are written by the engine
    before re-raising (INV 9). This helper standardizes caller handling."""
    from apps.audit.services import record as audit

    audit(
        actor=actor,
        action="RISK_EVALUATION_ERROR",
        metadata={
            "operation": str(operation_type),
            "strategy": resolve_failure(operation_type),
            "error": str(exc)[:200],
        },
    )
