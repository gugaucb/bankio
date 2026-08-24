"""LOGIN-specific risk metrics & backtest (spec PART 16/20).

Read-only aggregation over real RiskEvaluation rows produced by wired logins.
Honesty rules: no precision/recall without ground truth; intervention /
challenge / block rates are reported as measured on the available sample.
"""
from django.utils import timezone

from . import backtesting
from .models import RiskEvaluation

DECISIONS = ("ALLOW", "CHALLENGE", "REVIEW", "BLOCK")
LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def login_evaluations(window_hours=None):
    qs = RiskEvaluation.objects.filter(operation_type="LOGIN")
    if window_hours:
        qs = qs.filter(created_at__gte=timezone.now() - timezone.timedelta(hours=window_hours))
    return qs


def _rate(n, total):
    return round(n / total, 4) if total else None


def login_metrics(window_hours=24):
    """Distribution of decisions/levels, latency and engine errors for LOGIN."""
    from django.db.models import Count

    from apps.audit.models import AuditLog

    qs = login_evaluations(window_hours)
    by_decision = dict(qs.values_list("decision").annotate(n=Count("id")))
    by_level = dict(qs.exclude(risk_level="").values_list("risk_level").annotate(n=Count("id")))
    completed = qs.filter(completed_at__isnull=False)
    latencies = sorted(
        (e.completed_at - e.created_at).total_seconds() * 1000 for e in completed
    )
    errors_qs = AuditLog.objects.filter(action="RISK_EVALUATION_ERROR",
                                        metadata__operation="LOGIN")
    if window_hours:
        errors_qs = errors_qs.filter(
            timestamp__gte=timezone.now() - timezone.timedelta(hours=window_hours))
    errors = errors_qs.count()

    total = sum(by_decision.values())
    interventions = sum(by_decision.get(d, 0) for d in ("CHALLENGE", "REVIEW", "BLOCK"))
    return {
        "operation": "LOGIN",
        "window_hours": window_hours,
        "total_logins_evaluated": total,
        "decisions": {d: by_decision.get(d, 0) for d in DECISIONS},
        "levels": {lv: by_level.get(lv, 0) for lv in LEVELS},
        "latency_ms": {
            "p50": latencies[len(latencies) // 2] if latencies else None,
            "p95": latencies[int(len(latencies) * 0.95)] if latencies else None,
            "max": latencies[-1] if latencies else None,
        },
        "engine_errors": errors,
        "intervention_rate": _rate(interventions, total),
        "challenge_rate": _rate(by_decision.get("CHALLENGE", 0), total),
        "block_rate": _rate(by_decision.get("BLOCK", 0), total),
        "labels_available": False,   # no ground truth for logins yet
        "note": ("No fraud ground truth exists for logins; distribution and "
                 "intervention metrics only — precision/recall not fabricated."),
    }


def login_backtest(ruleset=None, limit=100000):
    """Replay stored LOGIN evaluation snapshots through a candidate ruleset."""
    qs = login_evaluations().filter(status=RiskEvaluation.Status.COMPLETED)
    if ruleset is None:  # default candidate = whatever is enabled in the bank
        from .models import RiskRule

        ruleset = RiskRule.objects.filter(enabled=True)
    result = backtesting.backtest(ruleset, evaluations=list(qs)[:limit])
    total = max(result["total"], 1)
    decisions = result["decisions"]
    result["operation"] = "LOGIN"
    result["rates"] = {
        "intervention_rate": _rate(
            sum(decisions.get(d, 0) for d in ("CHALLENGE", "REVIEW", "BLOCK")), total),
        "challenge_rate": _rate(decisions.get("CHALLENGE", 0), total),
        "block_rate": _rate(decisions.get("BLOCK", 0), total),
        "review_rate": _rate(decisions.get("REVIEW", 0), total),
    }
    result["gate"] = backtesting.enforcement_gate(result)
    return result
