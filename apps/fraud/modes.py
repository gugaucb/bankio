"""Engine modes and shadow-mode resolution (spec PART 8 / §35-37).

Modes: DISABLED -> SHADOW -> CHALLENGE_ONLY -> ENFORCEMENT (mandatory
progression). In SHADOW the full pipeline runs but nothing interferes with
Bankio operations. Mode changes are controlled and audited.
"""
from django.db import transaction

from apps.audit.services import record as audit

from .models import FraudEngineSetting, RiskEvaluation

MODE_KEY = "FRAUD_MODE"


class FraudModeError(ValueError):
    pass


def get_mode() -> str:
    row = FraudEngineSetting.objects.filter(key=MODE_KEY).first()
    if row is None:
        from django.conf import settings as dj_settings

        return getattr(dj_settings, "FRAUD_MODE", None) or RiskEvaluation.EngineMode.SHADOW
    return row.value


@transaction.atomic
def set_mode(new_mode, actor=None):
    if new_mode not in RiskEvaluation.EngineMode.values:
        raise FraudModeError(f"Unknown fraud mode {new_mode!r}")
    if actor is not None and not getattr(actor, "is_superuser", False):
        from .rbac import FraudPermissionDenied, has_permission

        if not has_permission(actor, "manage_policies"):
            raise FraudPermissionDenied("manage_policies required")
    old = get_mode()
    row, _ = FraudEngineSetting.objects.update_or_create(key=MODE_KEY, defaults={"value": new_mode})
    audit(
        action="FRAUD_MODE_CHANGED",
        resource=row,
        actor=actor,
        metadata={"from": old, "to": new_mode},
    )
    return new_mode


def effective_decision(evaluation) -> str:
    """Map the policy decision to what actually happens in the current mode.

    SHADOW / DISABLED never interfere; CHALLENGE_ONLY downgrades REVIEW and
    BLOCK to a step-up challenge instead of silent release.
    """
    mode = evaluation.engine_mode
    decision = evaluation.decision
    if mode in (RiskEvaluation.EngineMode.SHADOW, "DISABLED"):
        return DecisionProxy.ALLOWED
    if mode == RiskEvaluation.EngineMode.CHALLENGE_ONLY and decision in (
        RiskEvaluation.Decision.REVIEW,
        RiskEvaluation.Decision.BLOCK,
    ):
        return DecisionProxy.CHALLENGE
    return decision or DecisionProxy.ALLOWED


class DecisionProxy:
    ALLOWED = RiskEvaluation.Decision.ALLOW  # operation proceeds untouched
    CHALLENGE = RiskEvaluation.Decision.CHALLENGE
