"""Authentication risk signals (spec PART 20).

Geography is NOT reliably available in Bankio (no geo-IP source), so no
COUNTRY_CHANGE signal is fabricated — its absence is explicit (D-F04).
"""
from apps.audit.models import AuditLog
from django.utils import timezone

from .signals import register


@register("IP_DIFFERS_FROM_LAST_LOGIN")
def ip_differs_from_last_login(ctx, user=None):
    user = user or ctx.actor
    if user is None or not ctx.ip:
        return None
    last = (
        AuditLog.objects.filter(action="LOGIN", actor=user, ip_address__isnull=False)
        .order_by("-timestamp")
        .first()
    )
    if last is None:
        return None  # no baseline yet — unknown, not "changed"
    return last.ip_address != ctx.ip


@register("LOGIN_VELOCITY_15MIN")
def login_velocity_15min(ctx, user=None):
    user = user or ctx.actor
    if user is None:
        return None
    return AuditLog.objects.filter(
        Q_login(user),
        timestamp__gte=timezone.now() - timezone.timedelta(minutes=15),
    ).count()


def Q_login(user):
    from django.db.models import Q

    return Q(actor=user) & (Q(action="LOGIN") | Q(action="LOGIN_FAILED"))


# MFA failure count: identity records LOGIN_MFA on success and LOGIN_FAILED on
# bad credentials; OTP failures are recorded as LOGIN_MFA with metadata flag.
@register("MFA_FAILURE_COUNT_24H")
def mfa_failure_count_24h(ctx, user=None):
    user = user or ctx.actor
    if user is None:
        return None
    return AuditLog.objects.filter(
        actor=user,
        action="LOGIN_MFA",
        timestamp__gte=timezone.now() - timezone.timedelta(hours=24),
    ).count()
