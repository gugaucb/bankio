"""Fraud case workflow (spec PART 12).

Cases are investigations; CONFIRMED_FRAUD lives ONLY here (INVARIANT 6).
Every state change appends a timeline event — history is never overwritten.
"""
from django.db import transaction
from django.utils import timezone

from .models import (
    CASE_TRANSITIONS,
    CaseTransitionError,
    FraudAlert,
    FraudCase,
    FraudCaseEvent,
)


def _event(case, event_type, actor=None, **detail):
    return FraudCaseEvent.objects.create(case=case, event_type=event_type, actor=actor, detail=detail)


@transaction.atomic
def open_case(customer, alerts, severity="MEDIUM", actor=None, summary=""):
    alert_ids = [a.pk for a in alerts]
    case = FraudCase.objects.create(
        customer=customer, severity=severity, summary=summary,
        status=FraudCase.Status.OPEN,
    )
    FraudAlert.objects.filter(pk__in=alert_ids).update(status=FraudAlert.Status.ESCALATED)
    case.alerts.set(alerts)
    _event(case, "CASE_OPENED", actor, alerts=alert_ids)
    return case


@transaction.atomic
def claim(case, analyst):
    if case.status not in (FraudCase.Status.OPEN, FraudCase.Status.INVESTIGATING):
        raise CaseTransitionError("Case is not claimable.")
    case.assigned_analyst = analyst
    case.save(update_fields=["assigned_analyst"])
    _event(case, "ANALYST_ASSIGNED", analyst)
    return case


@transaction.atomic
def transition(case, new_status, actor=None, decision_reason=""):
    if new_status == case.status:
        raise CaseTransitionError("No-op transition.")
    if new_status not in CASE_TRANSITIONS.get(case.status, set()):
        raise CaseTransitionError(f"Illegal transition {case.status} -> {new_status}")
    if new_status in (FraudCase.Status.CONFIRMED_FRAUD, FraudCase.Status.FALSE_POSITIVE, FraudCase.Status.CLOSED):
        if not decision_reason:
            raise CaseTransitionError("Terminal transitions require a decision reason.")
        case.decision_reason = decision_reason
    if new_status in (FraudCase.Status.CLOSED, FraudCase.Status.CONFIRMED_FRAUD, FraudCase.Status.FALSE_POSITIVE):
        case.closed_at = timezone.now()
    case.status = new_status
    case.save()
    _event(case, f"TRANSITION_{new_status}", actor, reason=decision_reason or None)
    return case


def timeline(case):
    return list(case.events.all())
