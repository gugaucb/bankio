"""Task 36: challenge behavior measurement over stored data."""
import pytest
from django.utils import timezone

from apps.fraud.challenge_metrics import challenge_metrics
from apps.fraud.models import RiskChallenge, RiskEvaluation


def _eval(decision, mode=RiskEvaluation.EngineMode.CHALLENGE_ONLY, customer=None):
    return RiskEvaluation.objects.create(
        operation_type="TRANSFER", engine_mode=mode, customer=customer,
        status=RiskEvaluation.Status.COMPLETED, decision=decision,
    )


@pytest.mark.django_db
def test_counts_challenge_grade_by_operation():
    _eval(RiskEvaluation.Decision.BLOCK)
    _eval(RiskEvaluation.Decision.REVIEW)
    _eval(RiskEvaluation.Decision.ALLOW)
    _eval(RiskEvaluation.Decision.BLOCK, mode=RiskEvaluation.EngineMode.ENFORCEMENT)  # stays BLOCK
    m = challenge_metrics(window_hours=24)
    assert m["challenge_grade_evaluations"] == 2
    assert m["by_operation"] == {"TRANSFER": 2}


@pytest.mark.django_db
def test_challenge_outcomes_and_verification_rate(django_user_model):
    user = django_user_model.objects.create_user("cm-user", email="cmu@t.io", password="x")
    ev = _eval(RiskEvaluation.Decision.BLOCK, customer=user)
    ch = RiskChallenge.objects.create(
        customer=user, evaluation=ev, material_hash="h" * 64, code_hash="c" * 32,
        expires_at=timezone.now() + timezone.timedelta(minutes=5),
    )
    ch.status = RiskChallenge.Status.VERIFIED
    ch.save()
    RiskChallenge.objects.create(
        customer=user, evaluation=ev, material_hash="i" * 64, code_hash="d" * 32,
        expires_at=timezone.now() + timezone.timedelta(minutes=5),
        status=RiskChallenge.Status.EXPIRED,
    )
    m = challenge_metrics(window_hours=24)
    assert m["challenges"]["issued"] == 2
    assert m["verification_rate"] == 0.5


@pytest.mark.django_db
def test_empty_window_reports_none_not_zero(db):
    m = challenge_metrics(window_hours=24)
    assert m["challenge_grade_evaluations"] == 0
    assert m["challenges"]["issued"] == 0
    assert m["verification_rate"] is None
