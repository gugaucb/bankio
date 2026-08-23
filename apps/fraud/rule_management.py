"""Rule lifecycle management (spec PART 15 / §52-53).

Lifecycle: DRAFT -> TESTING -> APPROVED -> ACTIVE -> RETIRED.
Maker-checker: the creator of a rule version can never approve it.
Every transition is audited (INVARIANT 7). Activation requires the
`manage_rules` permission — analysts can never change enforcement (SoD).
"""
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record as audit

from .models import RiskEvaluation, RiskRule
from .rbac import has_permission


class RuleManagementError(ValueError):
    pass


def _require(actor, permission):
    if not has_permission(actor, permission):
        raise RuleManagementError(f"{permission} required")


@transaction.atomic
def create_draft(rule_id, name, score, actor=None, **fields):
    _require(actor, "manage_rules")
    rule = RiskRule.objects.create(
        rule_id=rule_id, name=name, score=score,
        enabled=False, lifecycle=RiskRule.Lifecycle.DRAFT,
        created_by=actor, **fields,
    )
    audit(actor=actor, action="RULE_CREATED", resource=rule,
          metadata={"rule_id": rule.rule_id, "version": rule.version})
    return rule


@transaction.atomic
def approve_rule(rule, actor=None):
    """APPROVED requires policy authority + maker-checker."""
    _require(actor, "manage_policies")
    if rule.lifecycle != RiskRule.Lifecycle.DRAFT and rule.lifecycle != RiskRule.Lifecycle.TESTING:
        raise RuleManagementError("Only DRAFT/TESTING rules can be approved.")
    if rule.created_by_id and rule.created_by_id == actor.pk:
        raise RuleManagementError("Maker-checker: the creator cannot approve their own rule.")
    rule.lifecycle = RiskRule.Lifecycle.APPROVED
    rule.approved_by = actor
    rule.save(update_fields=["lifecycle", "approved_by"])
    audit(actor=actor, action="RULE_APPROVED", resource=rule)
    return rule


@transaction.atomic
def activate_rule(rule, actor=None):
    _require(actor, "manage_policies")
    if rule.lifecycle != RiskRule.Lifecycle.APPROVED:
        raise RuleManagementError("Only APPROVED rules can be activated.")
    rule.lifecycle = RiskRule.Lifecycle.ACTIVE
    rule.enabled = True
    rule.effective_from = rule.effective_from or timezone.now()
    rule.save(update_fields=["lifecycle", "enabled", "effective_from"])
    audit(actor=actor, action="RULE_ACTIVATED", resource=rule,
          metadata={"rule_id": rule.rule_id, "version": rule.version})
    return rule


@transaction.atomic
def disable_rule(rule, actor=None):
    _require(actor, "manage_policies")
    rule.enabled = False
    rule.lifecycle = RiskRule.Lifecycle.RETIRED
    rule.save(update_fields=["enabled", "lifecycle"])
    audit(actor=actor, action="RULE_DISABLED", resource=rule,
          metadata={"rule_id": rule.rule_id, "version": rule.version})
    return rule


def simulate_rule(draft: RiskRule, limit=10000):
    """Replay candidate conditions over historical evaluation snapshots.

    Reports how many past operations would have triggered the rule and what
    decision they received. Read-only; never changes history.
    """
    from .rules import _condition_met

    conditions = draft.conditions if isinstance(draft.conditions, list) else [draft.conditions]
    conditions = [c for c in conditions if c]
    stats = {"evaluated": 0, "would_trigger": 0, "decisions": {}}
    for ev in RiskEvaluation.objects.filter(status=RiskEvaluation.Status.COMPLETED)[:limit]:
        stats["evaluated"] += 1
        values = dict(ev.signal_values or {})
        for s in ev.signals.all():
            values.setdefault(s.signal_id, s.value)
        if all(_condition_met(c, values) for c in conditions):
            stats["would_trigger"] += 1
            key = f"{ev.decision}:{ev.operation_type}"
            stats["decisions"][key] = stats["decisions"].get(key, 0) + 1
    return stats
