"""Policy engine (spec PART 7).

The score does not decide behavior — policy does. Each operation type may
carry its own level->decision mapping; unmapped levels fall back to the
DEFAULT policy. Policies are versioned so decisions remain explainable
after changes (INVARIANT 3).
"""
from .models import RiskEvaluation

Decision = RiskEvaluation.Decision
Level = RiskEvaluation.RiskLevel

# Default posture: escalating control with risk.
DEFAULT_POLICY = {
    Level.LOW: Decision.ALLOW,
    Level.MEDIUM: Decision.CHALLENGE,
    Level.HIGH: Decision.REVIEW,
    Level.CRITICAL: Decision.BLOCK,
}

POLICIES = {
    "DEFAULT": DEFAULT_POLICY,
    # Login: never send a login to human review mid-flow; challenge or block.
    "LOGIN": {
        Level.LOW: Decision.ALLOW,
        Level.MEDIUM: Decision.CHALLENGE,
        Level.HIGH: Decision.CHALLENGE,
        Level.CRITICAL: Decision.BLOCK,
    },
    # Profile/credential updates: high risk demands step-up, not silent block.
    "PROFILE_UPDATE": {
        Level.LOW: Decision.ALLOW,
        Level.MEDIUM: Decision.ALLOW,
        Level.HIGH: Decision.CHALLENGE,
        Level.CRITICAL: Decision.REVIEW,
    },
}

POLICY_VERSION = "policy-v1"


def policy_for(operation_type):
    return POLICIES.get(operation_type, POLICIES["DEFAULT"])


def decide(operation_type, risk_level):
    return policy_for(operation_type).get(risk_level, Decision.REVIEW)
