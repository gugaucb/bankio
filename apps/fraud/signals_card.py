"""Card fraud signals (spec PART 21).

Hard card controls (FROZEN, ONLINE_DISABLED, limits) remain business
controls in apps.cards and decline regardless of risk score; these signals
add behavioral visibility on top (rapid sequences, unusual patterns).
"""
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from .signals import register


@register("CARD_VELOCITY_10MIN")
def card_velocity_10min(ctx, card=None):
    if card is None:
        return None
    from apps.cards.models import CardTransaction

    return CardTransaction.objects.filter(
        card=card,
        created_at__gte=timezone.now() - timezone.timedelta(minutes=10),
    ).exclude(declined=True).count()


@register("CARD_DAILY_SPEND")
def card_daily_spend(ctx, card=None):
    if card is None:
        return None
    from apps.cards.models import CardTransaction

    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = (
        CardTransaction.objects.filter(card=card, declined=False, created_at__gte=start)
        .aggregate(s=Sum("amount"))["s"]
    )
    return str(total or Decimal("0"))


@register("CARD_RAPID_SEQUENCE")
def card_rapid_sequence(ctx, card=None):
    """True when >= 3 purchases within 2 minutes — classic card-testing burst."""
    if card is None:
        return None
    from apps.cards.models import CardTransaction

    return (
        CardTransaction.objects.filter(
            card=card,
            created_at__gte=timezone.now() - timezone.timedelta(minutes=2),
        ).count()
        >= 3
    )
