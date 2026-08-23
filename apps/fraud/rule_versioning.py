"""Rule versioning helpers (spec PART 5 / §30).

Rules are immutable once ACTIVE: changing behavior means creating a new
version. Historical RiskEvaluation rows keep the exact (rule_id, version)
pairs that fired, so auditors can always answer "why was this challenged?"
even after rules changed (INVARIANT 3 / INVARIANT 7).
"""
from django.db import transaction

from .models import RiskRule


def latest_version(rule_id):
    last = RiskRule.objects.filter(rule_id=rule_id).order_by("-version").first()
    return (last, last.version if last else 0)


@transaction.atomic
def new_rule_version(rule_id):
    """Clone the newest version of a rule into a DRAFT with version+1."""
    rule, version = latest_version(rule_id)
    if rule is None:
        raise ValueError(f"Unknown rule_id {rule_id!r}")
    return RiskRule.objects.create(
        rule_id=rule.rule_id,
        version=version + 1,
        name=rule.name,
        description=rule.description,
        enabled=False,
        lifecycle=RiskRule.Lifecycle.DRAFT,
        priority=rule.priority,
        operation_types=rule.operation_types,
        conditions=rule.conditions,
        score=rule.score,
        severity=rule.severity,
    )
