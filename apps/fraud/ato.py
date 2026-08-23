"""Account-takeover correlation (spec PART 22).

PASSWORD RESET -> NEW DEVICE -> PHONE/EMAIL CHANGE -> NEW BENEFICIARY ->
HIGH-VALUE TRANSFER is far more dangerous than any single event. The
correlation counts DISTINCT factor types in the window; each additional,
different factor escalates super-linearly. Output is explainable.
"""
from django.utils import timezone

ATO_WINDOW_HOURS = 48

FACTOR_AUDIT_ACTIONS = {
    "PASSWORD_CHANGED": "password_changed",
    "LOGIN_FAILED": "failed_logins",
    "MFA_EVENT": "mfa_event",
}

# distinct factors needed before correlation kicks in at all
MIN_FACTORS = 2


def _recent_audit_factors(user, since):
    from apps.audit.models import AuditLog

    rows = AuditLog.objects.filter(actor=user, timestamp__gte=since).values_list("action", flat=True)
    actions = set(rows)
    factors = {}
    if "PASSWORD_CHANGED" in actions:
        factors["password_changed"] = True
    failed = sum(1 for a in rows if a == "LOGIN_FAILED")
    if failed >= 3:
        factors["failed_logins"] = failed
    return factors


def _evaluation_factors(user, since):
    """Factors visible in recent risk evaluations (new device / new beneficiary)."""
    from apps.fraud.models import RiskEvaluation

    qs = RiskEvaluation.objects.filter(
        actor=user, created_at__gte=since, status=RiskEvaluation.Status.COMPLETED,
    )
    factors = {}
    for values in qs.values_list("signal_values", flat=True):
        v = values or {}
        if v.get("NEW_DEVICE") is True:
            factors["new_device"] = True
        if v.get("NEW_BENEFICIARY") is True or v.get("BENEFICIARY_IS_NEW") is True:
            factors["new_beneficiary"] = True
    return factors


def correlate_account_takeover(user, window_hours=ATO_WINDOW_HOURS):
    """Returns {factor_count, factors, ato_points, explanation}."""
    since = timezone.now() - timezone.timedelta(hours=window_hours)
    factors = {}
    factors.update(_recent_audit_factors(user, since))
    factors.update(_evaluation_factors(user, since))

    n = len(factors)
    if n < MIN_FACTORS:
        points = 0
    else:
        # super-linear: 30 for 2 factors, 55 for 3, 80 for 4, 100+ (clamped by scorer) after
        points = 5 + 25 * n
    explanation = (
        f"{n} correlated ATO factor(s) in the last {window_hours}h: "
        + ", ".join(sorted(factors))
    ) if factors else "no ATO factors observed"
    return {
        "factor_count": n,
        "factors": sorted(factors),
        "ato_points": min(points, 100),
        "explanation": explanation,
    }
