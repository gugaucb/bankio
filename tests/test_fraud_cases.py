"""Fraud cases: state machine, append-only timeline, fraud confirmation lives here."""
import pytest

from apps.fraud.cases import claim, open_case, timeline, transition
from apps.fraud.models import CaseTransitionError, FraudAlert, FraudCase, FraudCaseEvent


@pytest.fixture(autouse=True)
def clean(db):
    FraudCase.objects.all().delete()
    FraudAlert.objects.all().delete()
    yield


@pytest.fixture
def user(django_user_model, db):
    return django_user_model.objects.create_user("case-user", email="cu@t.io", password="x")


@pytest.fixture
def analyst(django_user_model, db):
    return django_user_model.objects.create_user("case-analyst", email="ca@t.io", password="x")


def _alert(user):
    return FraudAlert.objects.create(customer=user, alert_type="BLOCK:TRANSFER", severity="HIGH")


def test_open_case_attaches_alerts_and_escalates_them(user):
    a = _alert(user)
    case = open_case(user, [a], severity="HIGH", summary="suspicious pattern")
    assert case.status == FraudCase.Status.OPEN
    assert list(case.alerts.all()) == [a]
    a.refresh_from_db()
    assert a.status == FraudAlert.Status.ESCALATED
    assert any(e.event_type == "CASE_OPENED" for e in timeline(case))


def test_claim_records_analyst(user, analyst):
    case = open_case(user, [_alert(user)])
    claim(case, analyst)
    case.refresh_from_db()
    assert case.assigned_analyst == analyst
    assert any(e.event_type == "ANALYST_ASSIGNED" for e in timeline(case))


def test_full_investigation_to_confirmed_fraud_requires_reason(user, analyst):
    case = open_case(user, [_alert(user)])
    transition(case, FraudCase.Status.INVESTIGATING, actor=analyst)
    with pytest.raises(CaseTransitionError, match="reason"):
        transition(case, FraudCase.Status.CONFIRMED_FRAUD, actor=analyst)
    transition(case, FraudCase.Status.CONFIRMED_FRAUD,
               actor=analyst, decision_reason="customer confirmed unauthorized transfer")
    case.refresh_from_db()
    assert case.status == FraudCase.Status.CONFIRMED_FRAUD
    assert case.closed_at is not None


def test_illegal_transitions_blocked(user):
    case = open_case(user, [_alert(user)])
    with pytest.raises(CaseTransitionError):
        transition(case, FraudCase.Status.CONFIRMED_FRAUD)  # OPEN -> CONFIRMED illegal
    with pytest.raises(CaseTransitionError):
        transition(case, FraudCase.Status.OPEN)  # no-op


def test_terminal_states_are_absorbing(user, analyst):
    case = open_case(user, [_alert(user)])
    transition(case, FraudCase.Status.INVESTIGATING, actor=analyst)
    transition(case, FraudCase.Status.FALSE_POSITIVE, actor=analyst, decision_reason="legitimate")
    with pytest.raises(CaseTransitionError):
        transition(case, FraudCase.Status.INVESTIGATING, actor=analyst)


def test_timeline_is_append_only(user, analyst):
    case = open_case(user, [_alert(user)])
    event = case.events.first()
    with pytest.raises(ValueError):
        event.detail = {"tampered": True}
        event.save()
    with pytest.raises(ValueError):
        event.delete()
    before = len(timeline(case))
    transition(case, FraudCase.Status.INVESTIGATING, actor=analyst)
    assert len(timeline(case)) == before + 1  # history grows, never rewritten
