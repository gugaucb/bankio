"""Insider-risk correlation (spec PART 30).

Staff/manager activity is correlated for self-dealing patterns:
  - high volume of sensitive manager operations in the window
  - operations performed on accounts belonging to the manager itself
  - repeated REVIEW/BLOCK outcomes against the same actor

Deterministic, explainable, read-only — no enforcement here.
"""
from django.utils import timezone

INSIDER_WINDOW_HOURS = 24
MIN_FACTORS = 2
VOLUME_THRESHOLD = 5


def _operation_volume(user, since):
    from apps.fraud.models import RiskEvaluation

    return RiskEvaluation.objects.filter(
        actor=user, created_at__gte=since, status=RiskEvaluation.Status.COMPLETED,
    ).count()


def _self_dealing(user, since):
    """Manager operating on their own customer identity."""
    from apps.fraud.models import RiskEvaluation

    return RiskEvaluation.objects.filter(
        actor=user, created_at__gte=since,
        status=RiskEvaluation.Status.COMPLETED,
    ).exclude(customer=None).filter(customer=user).exists()


def _negative_outcomes(user, since):
    from apps.fraud.models import RiskEvaluation

    return RiskEvaluation.objects.filter(
        actor=user, created_at__gte=since,
        decision__in=[RiskEvaluation.Decision.REVIEW, RiskEvaluation.Decision.BLOCK],
    ).count()


def correlate_insider_risk(user, window_hours=INSIDER_WINDOW_HOURS):
    """Returns {factor_count, factors, insider_points, explanation}."""
    since = timezone.now() - timezone.timedelta(hours=window_hours)
    factors = {}

    volume = _operation_volume(user, since)
    if volume >= VOLUME_THRESHOLD:
        factors["high_operation_volume"] = volume
    if _self_dealing(user, since):
        factors["self_dealing"] = True
    negatives = _negative_outcomes(user, since)
    if negatives >= VOLUME_THRESHOLD:
        factors["repeated_negative_outcomes"] = negatives

    n = len(factors)
    points = 0 if n < MIN_FACTORS else min(5 + 25 * n, 100)
    explanation = (
        f"{n} insider factor(s) in the last {window_hours}h: "
        + ", ".join(sorted(factors))
    ) if factors else "no insider-risk factors observed"
    return {
        "factor_count": n,
        "factors": sorted(factors),
        "insider_points": points,
        "explanation": explanation,
    }
