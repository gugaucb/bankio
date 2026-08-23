"""Fraud alert creation with deduplication (spec PART 11).

An alert requires visibility; it is not an investigation (that's a case).
Correlated alerts within the dedup window collapse into one alert whose
occurrence count grows, preventing 500 identical alerts per incident.
"""
from datetime import timedelta

from django.utils import timezone

from .models import FraudAlert, RiskEvaluation

DEDUP_WINDOW_MINUTES = 60


def dedup_key(evaluation: RiskEvaluation) -> str:
    rules = ",".join(sorted(r["rule_id"] for r in evaluation.triggered_rules))
    customer = evaluation.customer_id or evaluation.actor_id or "anon"
    return f"{evaluation.operation_type}:{customer}:{rules}"[:200]


def raise_alert(evaluation: RiskEvaluation, alert_type=None):
    """Create (or correlate) an alert for a decision needing visibility."""
    if evaluation.decision not in (
        RiskEvaluation.Decision.REVIEW,
        RiskEvaluation.Decision.BLOCK,
        RiskEvaluation.Decision.DEFER,
    ):
        return None
    key = dedup_key(evaluation)
    window_start = timezone.now() - timedelta(minutes=DEDUP_WINDOW_MINUTES)
    existing = (
        FraudAlert.objects.filter(
            dedup_key=key,
            status=FraudAlert.Status.OPEN,
            created_at__gte=window_start,
        )
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        # correlated occurrence of the same underlying incident
        return existing
    severity = "HIGH" if evaluation.decision == RiskEvaluation.Decision.BLOCK else "MEDIUM"
    if evaluation.risk_level == RiskEvaluation.RiskLevel.CRITICAL:
        severity = "HIGH"
    return FraudAlert.objects.create(
        customer=evaluation.customer or evaluation.actor,
        evaluation=evaluation,
        alert_type=alert_type or f"{evaluation.decision}:{evaluation.operation_type}",
        severity=severity,
        dedup_key=key,
    )
