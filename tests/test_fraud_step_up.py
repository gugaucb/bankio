"""Step-up challenges: material binding, expiry, single use, no client trust."""
import pytest
from django.utils import timezone

from apps.fraud.challenge import (
    ChallengeError,
    consume_challenge,
    issue_challenge,
    verify_challenge,
)
from apps.fraud.models import RiskEvaluation


@pytest.fixture(autouse=True)
def clean(db):
    RiskEvaluation.objects.all().delete()
    yield


@pytest.fixture
def user(django_user_model, db):
    return django_user_model.objects.create_user("chl-user", email="chl@t.io", password="x")


def _evaluation(user):
    return RiskEvaluation.objects.create(
        operation_type="TRANSFER", actor=user,
        engine_mode=RiskEvaluation.EngineMode.ENFORCEMENT,
        decision=RiskEvaluation.Decision.CHALLENGE,
    )


FACTS = ("TRF-123", "500.00", "BEN-7")


def test_full_challenge_flow(user):
    ch, code = issue_challenge(_evaluation(user), user, FACTS)
    assert len(code) == 6 and ch.status == "PENDING"
    verify_challenge(ch, code, FACTS)
    assert ch.status == "VERIFIED"
    consume_challenge(ch, "TRF-123")
    assert ch.status == "CONSUMED"


def test_wrong_code_rejected_and_challenge_still_pending(user):
    ch, code = issue_challenge(_evaluation(user), user, FACTS)
    with pytest.raises(ChallengeError, match="INVALID_CODE"):
        verify_challenge(ch, "000000" if code != "000000" else "111111", FACTS)
    ch.refresh_from_db()
    assert ch.status == "PENDING"


def test_material_change_after_issuance_kills_challenge(user):
    """INV 5: amount changed after MFA -> challenge invalidated."""
    ch, code = issue_challenge(_evaluation(user), user, FACTS)
    with pytest.raises(ChallengeError, match="MATERIAL_CHANGED"):
        verify_challenge(ch, code, ("TRF-123", "9000.00", "BEN-7"))
    ch.refresh_from_db()
    assert ch.status == "EXPIRED"


def test_expired_challenge_rejected(db, user):
    from datetime import timedelta

    ch, code = issue_challenge(_evaluation(user), user, FACTS)
    from apps.fraud.models import RiskChallenge

    RiskChallenge.objects.filter(pk=ch.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    ch.refresh_from_db()
    with pytest.raises(ChallengeError, match="CHALLENGE_EXPIRED"):
        verify_challenge(ch, code, FACTS)


def test_challenge_cannot_be_reused_for_second_operation(user):
    """Client sending challenge_passed=true twice must fail server-side."""
    ch, code = issue_challenge(_evaluation(user), user, FACTS)
    verify_challenge(ch, code, FACTS)
    consume_challenge(ch, "TRF-123")
    with pytest.raises(ChallengeError, match="CHALLENGE_NOT_PENDING"):
        verify_challenge(ch, code, FACTS)  # replay


def test_consume_requires_verified_state(user):
    ch, _code = issue_challenge(_evaluation(user), user, FACTS)
    with pytest.raises(ChallengeError, match="NOT_VERIFIED"):
        consume_challenge(ch, "TRF-123")
