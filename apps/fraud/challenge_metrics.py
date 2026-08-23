"""Challenge behavior measurement (spec PART 36).

Answers, from stored data only: how many operations were downgraded to
CHALLENGE, what happened to issued challenges (verified / expired /
consumed / pending), and the verification rate. Read-only and honest —
rates are None when there is no denominator.
"""
from types import SimpleNamespace

from django.db.models import Count
from django.utils import timezone

from .models import RiskChallenge, RiskEvaluation
from .modes import effective_decision


def challenge_metrics(window_hours=24):
    since = timezone.now() - timezone.timedelta(hours=window_hours)

    by_operation = {}
    challenge_grade = 0
    qs = RiskEvaluation.objects.filter(
        created_at__gte=since, status=RiskEvaluation.Status.COMPLETED,
    ).values("operation_type", "decision", "engine_mode")
    for row in qs:
        effective = effective_decision(
            SimpleNamespace(decision=row["decision"], engine_mode=row["engine_mode"]))
        if effective == "CHALLENGE":
            challenge_grade += 1
            by_operation[row["operation_type"]] = by_operation.get(row["operation_type"], 0) + 1

    ch = RiskChallenge.objects.filter(created_at__gte=since)
    by_status = dict(ch.values_list("status").annotate(n=Count("id")))
    issued = sum(by_status.values())
    verified = by_status.get(RiskChallenge.Status.VERIFIED, 0) + by_status.get(
        RiskChallenge.Status.CONSUMED, 0)

    return {
        "window_hours": window_hours,
        "challenge_grade_evaluations": challenge_grade,
        "by_operation": by_operation,
        "challenges": {"issued": issued, "by_status": by_status},
        "verification_rate": round(verified / issued, 4) if issued else None,
    }
