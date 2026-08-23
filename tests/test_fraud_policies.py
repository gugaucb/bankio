"""Policy engine: operation-specific decisions, versioned, total mapping."""
import pytest

from apps.fraud.models import RiskEvaluation
from apps.fraud.policies import POLICY_VERSION, decide, policy_for

Level = RiskEvaluation.RiskLevel
Decision = RiskEvaluation.Decision


@pytest.mark.parametrize("level,expected", [
    (Level.LOW, Decision.ALLOW),
    (Level.MEDIUM, Decision.CHALLENGE),
    (Level.HIGH, Decision.REVIEW),
    (Level.CRITICAL, Decision.BLOCK),
])
def test_default_policy_escalates(level, expected):
    assert decide("TRANSFER", level) == expected


def test_login_policy_never_routes_to_review():
    for level in Level:
        assert decide("LOGIN", level) != Decision.REVIEW


def test_profile_update_high_risk_challenges_instead_of_blocking():
    assert decide("PROFILE_UPDATE", Level.HIGH) == Decision.CHALLENGE


def test_operations_may_use_different_policies_for_same_level():
    assert decide("LOGIN", Level.HIGH) != decide("TRANSFER", Level.HIGH)


def test_unknown_operation_falls_back_to_default():
    assert policy_for("MYSTERY_OP") is policy_for("DEFAULT")


def test_every_policy_covers_all_levels_with_safe_fallback():
    from apps.fraud.policies import POLICIES

    for name, mapping in POLICIES.items():
        missing = set(Level.values) - set(mapping)
        # unmapped levels fall back to REVIEW (fail-safe direction), never ALLOW
        for level in missing:
            assert decide(name if name != "DEFAULT" else "X-OP", level) == Decision.REVIEW


def test_policy_version_is_recorded():
    assert POLICY_VERSION.startswith("policy-")
