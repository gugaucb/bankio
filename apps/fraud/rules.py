"""Rule engine (spec PART 5).

Rules interpret collected signal FACTS. Evaluation is deterministic and
explainable: every trigger carries rule_id, version and the points it adds.

Condition shape (JSON, ANDed):
    {"signal": "NEW_BENEFICIARY", "op": "is", "value": True}
Operators: is, is_not, gt, lt, ge, le, in, not_in.
Only ACTIVE + enabled rules inside their effective window contribute.
"""
import hashlib

from django.utils import timezone

from .models import RiskRule

_OPS = {
    "is": lambda a, b: a == b,
    "is_not": lambda a, b: a != b,
    "gt": lambda a, b: _num(a) > _num(b),
    "lt": lambda a, b: _num(a) < _num(b),
    "ge": lambda a, b: _num(a) >= _num(b),
    "le": lambda a, b: _num(a) <= _num(b),
    "in": lambda a, b: a in (b or []),
    "not_in": lambda a, b: a not in (b or []),
}


def _num(v):
    from decimal import Decimal

    return Decimal(str(v))


def _condition_met(cond, signal_values):
    if not isinstance(cond, dict) or "signal" not in cond:
        return False  # malformed condition never triggers
    value = signal_values.get(cond["signal"])
    if isinstance(value, dict) and "__error__" in value:
        return False  # failed signals are unknown facts; rules do not fire on them
    op = _OPS.get(cond.get("op", "is"))
    if op is None:
        return False
    try:
        return bool(op(value, cond.get("value")))
    except Exception:
        return False


def eligible_rules(operation_type, now=None):
    now = now or timezone.now()
    qs = RiskRule.objects.filter(lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    out = []
    for r in qs.order_by("priority", "id"):
        if r.operation_types and operation_type not in r.operation_types:
            continue
        if r.effective_from and now < r.effective_from:
            continue
        if r.effective_until and now > r.effective_until:
            continue
        out.append(r)
    return out


def evaluate_rules(operation_type, signal_values, now=None):
    """Returns (triggered, ruleset_version).

    triggered: list of {rule_id, version, name, score, severity, conditions}
    ordered by priority then id — deterministic for identical inputs.
    """
    triggered = []
    seen_pairs = set()
    active_ids = []
    for rule in eligible_rules(operation_type, now):
        active_ids.append(f"{rule.rule_id}:{rule.version}")
        conditions = rule.conditions if isinstance(rule.conditions, list) else [rule.conditions]
        met = all(_condition_met(c, signal_values) for c in conditions)
        key = (rule.rule_id, rule.version)
        if met and key not in seen_pairs:
            seen_pairs.add(key)
            triggered.append({
                "rule_id": rule.rule_id,
                "version": rule.version,
                "name": rule.name,
                "score": rule.score,
                "severity": rule.severity,
                "conditions": conditions,
            })
    digest = hashlib.sha256("|".join(sorted(active_ids)).encode()).hexdigest()[:12]
    return triggered, f"rules-{digest}"
