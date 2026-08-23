"""Velocity signals (spec PART 4 / §25).

Efficient PostgreSQL aggregates over recent banking activity. Velocity is
measured on attempted money movement (all non-failed transfers) so that
rapid-fire attack sequences are visible even before settlement.
"""
from django.db.models import Sum
from django.utils import timezone

from .signals import register


def _recent_transfers(source_account, seconds):
    from apps.transfers.models import Transfer, TransferStatus

    return Transfer.objects.filter(
        source_account=source_account,
        created_at__gte=timezone.now() - timezone.timedelta(seconds=seconds),
    ).exclude(status=TransferStatus.FAILED)


@register("TRANSFER_VELOCITY_10MIN")
def transfers_last_10min(ctx, source_account=None):
    if source_account is None:
        return None
    return _recent_transfers(source_account, 600).count()


@register("TRANSFER_VELOCITY_1H")
def transfers_last_hour(ctx, source_account=None):
    if source_account is None:
        return None
    return _recent_transfers(source_account, 3600).count()


@register("TRANSFER_VELOCITY_24H")
def transfers_last_24h(ctx, source_account=None):
    if source_account is None:
        return None
    return _recent_transfers(source_account, 86400).count()


@register("TRANSFER_TOTAL_1H")
def transfer_total_last_hour(ctx, source_account=None):
    if source_account is None:
        return None
    agg = _recent_transfers(source_account, 3600).aggregate(total=Sum("amount"))
    return str(agg["total"] or 0)


@register("DAILY_TRANSFER_TOTAL")
def daily_transfer_total(ctx, source_account=None):
    """Calendar-day outflow total (mirrors the banking daily-limit window)."""
    if source_account is None:
        return None
    from apps.transfers.models import Transfer, TransferStatus

    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    agg = Transfer.objects.filter(
        source_account=source_account, created_at__gte=start
    ).exclude(status=TransferStatus.FAILED).aggregate(total=Sum("amount"))
    return str(agg["total"] or 0)


@register("NEW_BENEFICIARIES_24H")
def new_beneficiaries_24h(ctx, user=None):
    user = user or ctx.actor
    if user is None:
        return None
    from apps.accounts.models import Beneficiary

    return Beneficiary.objects.filter(
        owner=user, created_at__gte=timezone.now() - timezone.timedelta(hours=24)
    ).count()
