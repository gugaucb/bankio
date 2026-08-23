"""Rule versioning: new versions never rewrite history (INV 3, INV 7)."""
import pytest

from apps.fraud.models import RiskEvaluation, RiskRule
from apps.fraud.rule_versioning import latest_version, new_rule_version
from apps.fraud.rules import evaluate_rules


@pytest.fixture(autouse=True)
def clean(db):
    yield
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()


def _rule(rule_id, score, **kw):
    return RiskRule.objects.create(
        rule_id=rule_id, name=rule_id, score=score,
        lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True, **kw,
    )


def test_new_version_is_draft_and_increments(db):
    v1 = _rule("R", score=10)
    v2 = new_rule_version("R")
    assert v2.version == 2 and v2.lifecycle == RiskRule.Lifecycle.DRAFT
    assert not v2.enabled and v2.score == 10 and v2.pk != v1.pk


def test_unknown_rule_rejected(db):
    with pytest.raises(ValueError):
        new_rule_version("GHOST")


def test_only_active_version_scores_at_a_time(db):
    _rule("R", score=10)
    new_rule_version("R")  # draft must not contribute
    triggered, _ = evaluate_rules("TRANSFER", {})
    assert [t["version"] for t in triggered] == [1]


def test_historical_decision_keeps_original_versions(db):
    """Auditor question: 'why was this challenged?' — answer frozen at decision time."""
    _rule("R", score=25)
    triggered, rsv = evaluate_rules("TRANSFER", {"X": True})
    ev = RiskEvaluation.objects.create(
        operation_type="TRANSFER",
        engine_mode=RiskEvaluation.EngineMode.ENFORCEMENT,
        risk_score=25, risk_level=RiskEvaluation.RiskLevel.MEDIUM,
        decision=RiskEvaluation.Decision.CHALLENGE,
        policy_version="policy-v1", ruleset_version=rsv,
        triggered_rules=triggered, status=RiskEvaluation.Status.COMPLETED,
        completed_at=None,
    )
    from django.utils import timezone

    ev.completed_at = timezone.now()
    ev.save()

    # rule later changes to a harsher v2
    v2 = new_rule_version("R")
    v2.score = 90
    v2.enabled = True
    v2.lifecycle = RiskRule.Lifecycle.ACTIVE
    v2.save()
    RiskRule.objects.filter(pk=ev.pk)  # noop guard

    # historical evaluation is untouched by the rule change
    ev.refresh_from_db()
    assert ev.triggered_rules[0]["score"] == 25
    assert ev.ruleset_version == rsv
