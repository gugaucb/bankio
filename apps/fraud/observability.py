"""Engine observability (spec PART 32).

Read-only metrics over the evaluation store: decision distribution,
failure/error rates and evaluation latency percentiles. No external
monitoring service is introduced (single-repo constraint) — the fraud
console and tests consume this module directly.
"""
from django.db.models import Count
from django.utils import timezone

from apps.audit.models import AuditLog

from .models import RiskEvaluation

# performance budget (spec PART 32): engine must stay well under the
# request budget of the flows it observes
BUDGET_P95_MS = 200


def _latency_ms(qs):
    deltas = []
    for created, completed in qs.values_list("created_at", "completed_at"):
        if created and completed:
            deltas.append((completed - created).total_seconds() * 1000)
    if not deltas:
        return None
    deltas.sort()
    def pct(p):
        return round(deltas[min(len(deltas) - 1, int(len(deltas) * p))], 1)
    return {"p50": pct(0.50), "p95": pct(0.95), "max": round(deltas[-1], 1), "samples": len(deltas)}


def engine_metrics(window_hours=24):
    """Aggregate engine health over the window. Explainable, cheap, read-only."""
    since = timezone.now() - timezone.timedelta(hours=window_hours)
    qs = RiskEvaluation.objects.filter(created_at__gte=since)
    by_decision = dict(qs.values_list("decision").annotate(n=Count("id")))
    by_status = dict(qs.values_list("status").annotate(n=Count("id")))
    by_mode = dict(qs.values_list("engine_mode").annotate(n=Count("id")))

    from apps.audit.models import AuditLog

    errors = AuditLog.objects.filter(
        action="RISK_EVALUATION_ERROR", timestamp__gte=since
    ).count()

    total = sum(by_decision.values())
    completed = qs.filter(completed_at__isnull=False)
    latency = _latency_ms(completed)
    return {
        "window_hours": window_hours,
        "total_evaluations": total,
        "by_decision": by_decision,
        "by_status": by_status,
        "by_mode": by_mode,
        "engine_errors": errors,
        "latency_ms": latency,
        "budget_p95_ms": BUDGET_P95_MS,
        "within_budget": bool(latency) and latency["p95"] <= BUDGET_P95_MS,
    }
