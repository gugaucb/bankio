"""Authentication risk evaluation service (spec PART 20).

Outcomes: ALLOW / CHALLENGE / BLOCK (temporary) — driven by the LOGIN
policy. In SHADOW mode the result is recorded only.
"""
from .context import RiskContext
from .engine import evaluate_operation


def evaluate_login(user, request=None, device_id="", ip=""):
    """Run the LOGIN operation through the engine; returns RiskEvaluation."""
    ctx = RiskContext(
        operation_type="LOGIN",
        actor=user,
        customer=user,
        device_id=device_id or "",
        ip=ip,
    )
    return evaluate_operation(ctx)
