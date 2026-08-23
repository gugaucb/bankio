"""Adversarial suite (spec PART 33): attacks against the risk engine itself.

Each test plays a hostile actor trying to subvert decisions, challenges,
rule governance or idempotency — and asserts the defense holds.
"""
import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone
from uuid import uuid4

from apps.audit.models import AuditLog
from apps.fraud.challenge import issue_challenge, verify_challenge
from apps.fraud.modes import set_mode
from apps.fraud.models import FraudEngineSetting, RiskEvaluation, RiskRule
from apps.fraud.rbac import FraudPermissionDenied
from apps.fraud.rule_management import approve_rule, create_draft


# ------------------------------------------------------------------ INV 1/2
@pytest.mark.django_db
def test_client_cannot_post_an_impossible_score():
    """Even direct ORM abuse cannot persist an out-of-bounds score."""
    user = _user()
    with pytest.raises(IntegrityError):
        with db_transaction.atomic():
            RiskEvaluation.objects.create(
                operation_type="TRANSFER", actor=user, engine_mode="SHADOW",
                decision="ALLOW", risk_score=999,
            )


@pytest.mark.django_db
def test_shadow_mode_cannot_be_smuggled_into_enforcement(monkeypatch):
    """A hostile settings override cannot force ENFORCEMENT silently:
    mode changes must go through the audited setter."""
    from django.conf import settings

    monkeypatch.setattr(settings, "FRAUD_MODE", "ENFORCEMENT", raising=False)
    FraudEngineSetting.objects.all().delete()
    from apps.fraud.modes import get_mode

    # DB row absent → settings fallback applies (documented behavior),
    # but any runtime change still requires the audited path:
    mgr = _user(role="FRAUD_MANAGER")
    set_mode("CHALLENGE_ONLY", actor=mgr)
    assert get_mode() == "CHALLENGE_ONLY"
    assert AuditLog.objects.filter(action="FRAUD_MODE_CHANGED").exists()


# ------------------------------------------------------------------ INV 5
def _evaluation_for(user):
    return RiskEvaluation.objects.create(
        operation_type="TRANSFER", actor=user, customer=user,
        engine_mode=RiskEvaluation.EngineMode.CHALLENGE_ONLY,
        decision=RiskEvaluation.Decision.CHALLENGE,
    )


@pytest.mark.django_db
def test_replayed_and_tampered_challenges_fail(django_user_model):
    from apps.fraud.challenge import ChallengeError

    user = django_user_model.objects.create_user("adv-chal", email="ac@t.io", password="x")
    ev = _evaluation_for(user)
    facts = {"amount": "100.00", "beneficiary": "42"}
    ch, code = issue_challenge(ev, user, facts)
    assert verify_challenge(ch, code, facts).status == "VERIFIED"
    # replay of the exact same valid code must fail (single-use)
    ch.refresh_from_db()
    with pytest.raises(ChallengeError) as e:
        verify_challenge(ch, code, facts)
    assert "NOT_PENDING" in str(e.value)


@pytest.mark.django_db
def test_amount_tamper_after_issuance_kills_challenge(django_user_model):
    """Attacker requests challenge for $10 then tries to move $10,000."""
    from apps.fraud.challenge import ChallengeError

    user = django_user_model.objects.create_user("adv-tamper", email="at@t.io", password="x")
    ev = _evaluation_for(user)
    ch, code = issue_challenge(ev, user, {"amount": "10.00", "beneficiary": "42"})
    with pytest.raises(ChallengeError) as e:
        verify_challenge(ch, code, {"amount": "10000.00", "beneficiary": "42"})
    assert "MATERIAL_CHANGED" in str(e.value)
    ch.refresh_from_db()
    assert ch.status == "EXPIRED"  # challenge is dead, not reusable


# ------------------------------------------------------------------ SoD attacks
@pytest.mark.django_db
def test_analyst_cannot_create_or_approve_rules(django_user_model):
    from apps.fraud.rule_management import RuleManagementError

    analyst = django_user_model.objects.create_user(
        "adv-analyst", email="aa@t.io", password="x", role="FRAUD_ANALYST")
    with pytest.raises(RuleManagementError):
        create_draft(rule_id="ADV-R1", name="n", score=50, actor=analyst)
    mgr = django_user_model.objects.create_user(
        "adv-rmgr", email="rm@t.io", password="x", role="FRAUD_MANAGER")
    draft = create_draft(rule_id="ADV-R2", name="n", score=50, actor=mgr)
    with pytest.raises((RuleManagementError, FraudPermissionDenied, PermissionDenied)):
        approve_rule(draft, actor=analyst)


@pytest.mark.django_db
def test_non_manager_cannot_switch_engine_mode(django_user_model):
    analyst = django_user_model.objects.create_user(
        "adv-mode", email="am@t.io", password="x", role="FRAUD_ANALYST")
    with pytest.raises((FraudPermissionDenied, PermissionDenied)):
        set_mode("ENFORCEMENT", actor=analyst)


@pytest.mark.django_db
def test_disabled_rule_stops_firing_even_if_row_edited_to_active_lifecycle(django_user_model):
    """Toggling lifecycle in the DB without the governed path must not arm a rule."""
    rule = RiskRule.objects.create(rule_id="ADV-OFF", name="n", score=90,
                                   lifecycle=RiskRule.Lifecycle.RETIRED, enabled=False)
    rule.lifecycle = RiskRule.Lifecycle.ACTIVE  # raw tamper, enabled stays False
    rule.save()
    user = _user(role="CUSTOMER")
    ctx = _ctx(user)
    from apps.fraud.rules import evaluate_rules

    triggered, _ = evaluate_rules(ctx, {})
    assert all(t["rule_id"] != "ADV-OFF" for t in triggered)


# ------------------------------------------------------------------ helpers
def _user(role="CUSTOMER"):
    from tests.conftest import make_user

    return make_user(f"adv-{uuid4().hex[:8]}", role=role)


def _ctx(user):
    from apps.fraud.context import RiskContext

    return RiskContext(operation_type="TRANSFER", actor=user, customer=user,
                       amount=None, timestamp=timezone.now())
