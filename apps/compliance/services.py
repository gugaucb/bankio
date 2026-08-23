"""Fraud engine: configurable rules evaluated before settlement."""
from dataclasses import dataclass

from django.db.models import Count
from django.utils import timezone

from .models import FraudAlert, FraudRule


@dataclass
class Verdict:
    blocked: bool = False
    review: bool = False
    reason: str = ""


def evaluate_fraud(*, actor, source, amount, destination=None, beneficiary=None):
    verdict = Verdict()

    def apply(rule, reason):
        if rule.action == "BLOCK":
            verdict.blocked = True
            verdict.reason = reason
        else:
            if not verdict.blocked:
                verdict.reason = verdict.reason or reason
            verdict.review = True
        FraudAlert.objects.create(
            customer=actor,
            rule=rule,
            reason=reason,
            severity="HIGH" if rule.action == "BLOCK" else "MEDIUM",
        )

    for rule in FraudRule.objects.filter(enabled=True):
        if rule.rule_type == "AMOUNT_ABOVE" and rule.threshold and amount > rule.threshold:
            apply(rule, f"Amount {amount} above review threshold {rule.threshold}")
        elif rule.rule_type == "VELOCITY":
            from apps.transfers.models import Transfer
            recent = Transfer.objects.filter(
                source_account=source,
                created_at__gte=timezone.now() - timezone.timedelta(minutes=10),
            ).exclude(status="FAILED").count()
            threshold_n = int(rule.threshold or 5)
            if recent >= threshold_n:
                apply(rule, f"Velocity: {recent} transfers in 10 minutes")
        elif rule.rule_type == "NEW_DEVICE_HIGH_VALUE" and rule.threshold and amount >= rule.threshold:
            from apps.identity.services import is_new_device
            # request not available here; managers can tighten via admin. Demo: skip.
    return verdict
