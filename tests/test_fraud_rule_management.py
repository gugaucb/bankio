"""Rule lifecycle: authorization, maker-checker, audit, pre-activation simulation."""
import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.fraud.models import RiskEvaluation, RiskRule
from apps.fraud.rule_management import (
    RuleManagementError,
    activate_rule,
    approve_rule,
    create_draft,
    disable_rule,
    simulate_rule,
)
from apps.fraud.rbac import FraudPermissionDenied


@pytest.fixture(autouse=True)
def clean(db):
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    AuditLog.objects.filter(action__startswith="RULE_").delete()
    yield


def _user(django_user_model, role, username):
    return django_user_model.objects.create_user(
        username, email=f"{username}@t.io", password="x", role=role,
    )


@pytest.fixture
def maker(django_user_model, db):
    # a manager creates AND approves in tests where SoD isn't the subject;
    # maker != checker enforced separately below
    return _user(django_user_model, "FRAUD_MANAGER", "rm-maker")


def test_only_policy_managers_manage_rules(django_user_model, db):
    analyst = _user(django_user_model, "FRAUD_ANALYST", "rm-analyst")
    with pytest.raises(RuleManagementError):
        create_draft("R-X", "x", 10, actor=analyst)


def test_full_lifecycle_is_audited(django_user_model, db, maker):
    checker = _user(django_user_model, "FRAUD_MANAGER", "rm-checker")
    rule = create_draft("R-LIFE", "life", 25, actor=maker)
    approve_rule(rule, actor=checker)
    activate_rule(rule, actor=checker)
    rule.refresh_from_db()
    assert rule.lifecycle == RiskRule.Lifecycle.ACTIVE and rule.enabled
    for action in ("RULE_CREATED", "RULE_APPROVED", "RULE_ACTIVATED"):
        assert AuditLog.objects.filter(action=action).exists()


def test_maker_checker_blocks_self_approval(django_user_model, db, maker):
    rule = create_draft("R-SOD", "sod", 10, actor=maker)
    with pytest.raises(RuleManagementError, match="[Mm]aker"):
        approve_rule(rule, actor=maker)


def test_cannot_activate_unapproved_or_disable_twice(django_user_model, db, maker):
    checker = _user(django_user_model, "FRAUD_MANAGER", "rm-checker2")
    rule = create_draft("R-ORD", "ord", 10, actor=maker)
    with pytest.raises(RuleManagementError):
        activate_rule(rule, actor=checker)  # DRAFT cannot jump to ACTIVE
    approve_rule(rule, actor=checker)
    activate_rule(rule, actor=checker)
    disable_rule(rule, actor=checker)
    assert rule.lifecycle == RiskRule.Lifecycle.RETIRED
    assert not rule.enabled
    # retired rules never score (engine only takes ACTIVE)
    from apps.fraud.rules import evaluate_rules

    assert evaluate_rules("TRANSFER", {})[0] == []


def test_simulation_reports_impact_without_changing_history(django_user_model, db):
    RiskEvaluation.objects.create(
        operation_type="TRANSFER",
        engine_mode=RiskEvaluation.EngineMode.SHADOW,
        decision=RiskEvaluation.Decision.ALLOW,
        risk_score=0, risk_level=RiskEvaluation.RiskLevel.LOW,
        signal_values={"NEW_BENEFICIARY": True},
        status=RiskEvaluation.Status.COMPLETED,
        triggered_rules=[],
    )
    RiskEvaluation.objects.create(
        operation_type="CARD_PURCHASE",
        engine_mode=RiskEvaluation.EngineMode.SHADOW,
        decision=RiskEvaluation.Decision.ALLOW,
        risk_score=0, risk_level=RiskEvaluation.RiskLevel.LOW,
        signal_values={"NEW_BENEFICIARY": False},
        status=RiskEvaluation.Status.COMPLETED,
        triggered_rules=[],
    )
    draft = RiskRule.objects.create(
        rule_id="R-SIM", name="sim", score=30,
        lifecycle=RiskRule.Lifecycle.DRAFT,
        conditions=[{"signal": "NEW_BENEFICIARY", "op": "is", "value": True}],
    )
    stats = simulate_rule(draft)
    assert stats["evaluated"] == 2
    assert stats["would_trigger"] == 1
    assert stats["decisions"] == {"ALLOW:TRANSFER": 1}
    # history untouched
    assert RiskEvaluation.objects.count() == 2
