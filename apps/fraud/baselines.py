"""Explainable customer behavior baselines (spec PART 17).

Baselines describe what is NORMAL for a customer: typical transfer size,
usual hours, usual beneficiaries. Small samples are flagged unreliable
instead of producing bad statistics (§57).
"""
from decimal import Decimal

from django.db.models import Avg, Count, Max
from django.utils import timezone

MIN_OBSERVATIONS = 5


def transfer_baseline(customer, lookback_days=90):
    from apps.transfers.models import Transfer, TransferStatus

    since = timezone.now() - timezone.timedelta(days=lookback_days)
    qs = (
        Transfer.objects.filter(
            source_account__customer=customer,
            created_at__gte=since,
            status=TransferStatus.COMPLETED,
        )
        .exclude(status=TransferStatus.REVERSED)
    )
    agg = qs.aggregate(n=Count("id"), avg=Avg("amount"), largest=Max("amount"))
    n = agg["n"] or 0
    amounts = sorted(Decimal(str(a)) for a in qs.values_list("amount", flat=True))
    median = None
    if amounts:
        mid = len(amounts) // 2
        median = str((amounts[mid] if len(amounts) % 2 else (amounts[mid - 1] + amounts[mid]) / 2))
    hour_counts = {}
    for t in qs.only("created_at"):
        hour_counts[t.created_at.hour] = hour_counts.get(t.created_at.hour, 0) + 1
    return {
        "sample_size": n,
        "reliable": n >= MIN_OBSERVATIONS,
        "avg_amount": str(agg["avg"]) if agg["avg"] is not None else None,
        "median_amount": median,
        "largest_normal_transfer": str(agg["largest"]) if agg["largest"] is not None else None,
        "typical_hours": [h for h, _ in sorted(hour_counts.items(), key=lambda kv: -kv[1])[:4]],
        "min_observations_required": MIN_OBSERVATIONS,
    }


def amount_multiplier_vs_baseline(customer, amount):
    """How many times the customer's average transfer is this amount.
    Returns None when the baseline is unreliable — absence of evidence,
    not a fabricated multiplier."""
    base = transfer_baseline(customer)
    if not base["reliable"] or not base["avg_amount"]:
        return None
    avg = Decimal(base["avg_amount"])
    if avg <= 0:
        return None
    return round(float(Decimal(str(amount)) / avg), 2)


def is_unusual_hour(customer, hour):
    base = transfer_baseline(customer)
    if not base["reliable"]:
        return False  # cannot judge without a reliable pattern
    typical = set(base["typical_hours"])
    return bool(typical) and hour not in typical
