"""Beneficiary risk signals (spec PART 19).

NEW BENEFICIARY + NEW DEVICE + HIGH AMOUNT is the classic account-takeover
drain pattern; these signals make each fact individually visible so rules
can combine them explainably.
"""
from django.db.models import Count, Sum
from django.utils import timezone

from .signals import register


def _beneficiary(beneficiary):
    return beneficiary  # domain object passed by the transfer flow


@register("BENEFICIARY_AGE_HOURS")
def beneficiary_age_hours(ctx, beneficiary=None):
    b = _beneficiary(beneficiary)
    if b is None:
        return None
    age = (ctx.timestamp - b.created_at).total_seconds() / 3600
    return round(age, 2)


@register("BENEFICIARY_IS_NEW")
def beneficiary_is_new(ctx, beneficiary=None, max_age_hours=1.0):
    age = beneficiary_age_hours(ctx, beneficiary=beneficiary)
    if age is None:
        return None
    return age < max_age_hours


@register("FIRST_TRANSFER_TO_BENEFICIARY")
def first_transfer_to_beneficiary(ctx, source_account=None, beneficiary=None):
    if source_account is None or beneficiary is None:
        return None
    from apps.transfers.models import Transfer, TransferStatus

    prior = Transfer.objects.filter(
        source_account=source_account,
        beneficiary=beneficiary,
        created_at__lt=ctx.timestamp,
    ).exclude(status=TransferStatus.FAILED).exists()
    return not prior


@register("BENEFICIARY_TRANSFERS_24H")
def beneficiary_transfers_24h(ctx, source_account=None, beneficiary=None):
    if source_account is None or beneficiary is None:
        return None
    from apps.transfers.models import Transfer, TransferStatus

    qs = Transfer.objects.filter(
        source_account=source_account,
        beneficiary=beneficiary,
        created_at__gte=timezone.now() - timezone.timedelta(hours=24),
    ).exclude(status=TransferStatus.FAILED)
    agg = qs.aggregate(n=Count("id"), total=Sum("amount"))
    return {"count": agg["n"] or 0, "total": str(agg["total"] or 0)}
