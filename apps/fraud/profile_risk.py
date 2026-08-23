"""Sensitive profile-change risk (spec PART 27).

Password changes and contact-detail updates are prime ATO steps. In
SHADOW the engine observes; enforcement wiring comes with the cutover.
"""
from .context import RiskContext


def evaluate_profile_change(user, *, request=None, operation_type="PROFILE_UPDATE"):
    """Run a profile-change operation through the engine; never fatal."""
    from .engine import evaluate_operation

    ctx = RiskContext(
        operation_type=operation_type,
        actor=user,
        customer=user,
        device_id=_device_hash(request) if request else "",
        ip=request.META.get("REMOTE_ADDR", "") if request else "",
    )
    try:
        return evaluate_operation(ctx)
    except Exception as exc:
        from apps.audit.services import record as audit

        audit(
            action="RISK_EVALUATION_ERROR",
            actor=user,
            metadata={"scope": "profile_change", "operation": operation_type, "error": str(exc)[:200]},
        )
        return None


def _device_hash(request):
    from apps.identity.services import _device_hash as identity_hash

    return identity_hash(request)
