"""Branch 3b — safe card-purchase resume after a step-up challenge."""
import logging
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.accounts.models import Account, AccountStatus
from apps.cards.models import Card, CardStatus, CardType
from apps.cards.services import CardDeclined, purchase, resume_purchase
from apps.fraud import modes
from apps.fraud.models import FraudEngineSetting, RiskChallenge, RiskEvaluation, RiskRule
from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry
from apps.notifications.models import Notification


@pytest.fixture(autouse=True)
def clean_engine(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    FraudEngineSetting.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield
    FraudEngineSetting.objects.all().delete()


@pytest.fixture(autouse=True)
def oob_capture():
    class _Sink(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.INFO)
            self.records = []

        def emit(self, record):
            self.records.append(record)

    sink = _Sink()
    lg = logging.getLogger("bankio.challenge")
    lg.addHandler(sink)
    lg.setLevel(logging.INFO)
    yield sink
    lg.removeHandler(sink)


def _code_of(sink, challenge_id):
    for record in sink.records:
        msg = record.getMessage()
        if f"CHL-{challenge_id} " in msg:
            return msg.rsplit(": ", 1)[1].split()[0]
    raise AssertionError(f"no delivered code for challenge {challenge_id}")


@pytest.fixture
def challenged_card(db, django_user_model):
    user = django_user_model.objects.create_user("card-user", email="cu@t.io", password="x")
    cash = ledger.get_or_create_account(f"CD-CASH-{uuid4().hex[:6]}", "Cash", type="ASSET")
    la = ledger.get_or_create_account(f"2001-CD-{user.username}", "Deposit", is_customer=True)
    ledger.post_journal(f"CD-DEP-{uuid4().hex[:8]}", "dep",
                        [(cash, "DEBIT", Decimal("5000.00")), (la, "CREDIT", Decimal("5000.00"))])
    acct = Account.objects.create(customer=user, account_number=f"53{user.pk:010d}",
                                  status=AccountStatus.ACTIVE, ledger_account=la)
    card = Card.objects.create(account=acct, type=CardType.DEBIT, status=CardStatus.ACTIVE,
                               last4="4242", holder_name=user.username,
                               tx_limit=Decimal("1000.00"), daily_limit=Decimal("2000.00"))
    RiskRule.objects.create(rule_id="CD-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    mgr = django_user_model.objects.create_user("cd-mgr", email="cm@t.io", password="x",
                                                role="FRAUD_MANAGER")
    modes.set_mode(RiskEvaluation.EngineMode.CHALLENGE_ONLY, actor=mgr)
    return user, acct, card


def _start_purchase(card, key):
    try:
        return purchase(card_id=card.pk, merchant="Book Store",
                        amount_raw="30.00", online=True, idempotency_key=key)
    except CardDeclined as e:
        assert e.args[0] == "STEP_UP_REQUIRED"
        return e


# ------------------------------------------------------------------- tests

@pytest.mark.django_db
def test_purchase_stops_with_challenge_and_decline_row(challenged_card, oob_capture):
    from apps.cards.models import CardTransaction

    user, acct, card = challenged_card
    key = f"CD-{uuid4().hex[:10]}"
    err = _start_purchase(card, key)
    ch = RiskChallenge.objects.get(pk=err.challenge_id)
    assert ch.status == RiskChallenge.Status.PENDING
    assert ch.evaluation.operation_type == "CARD_PURCHASE"
    assert CardTransaction.objects.filter(card=card, declined=True,
                                          decline_reason="STEP_UP_REQUIRED").exists()
    note = Notification.objects.get(recipient=user, category="SECURITY")
    assert "verification code was sent" in note.body.lower()


@pytest.mark.django_db
def test_resume_settles_exactly_once(challenged_card, oob_capture):
    from apps.cards.models import CardTransaction

    user, acct, card = challenged_card
    key = f"CD-{uuid4().hex[:10]}"
    err = _start_purchase(card, key)
    code = _code_of(oob_capture, err.challenge_id)
    bal = account_balance_of(acct)

    tx = resume_purchase(card_id=card.pk, merchant="Book Store", amount="30.00",
                         facts=dict(err.facts), code=code, challenge_id=err.challenge_id,
                         online=True)
    assert tx.declined is False and tx.amount == Decimal("30.00")
    ch = RiskChallenge.objects.get(pk=err.challenge_id)
    assert ch.status == RiskChallenge.Status.CONSUMED
    assert account_balance_of(acct) == bal - Decimal("30.00")   # moved exactly once
    settled = CardTransaction.objects.filter(card=card, declined=False).count()
    assert settled == 1
    # replay via the same idempotency marker returns the same transaction
    tx2 = purchase(card_id=card.pk, merchant="Book Store", amount_raw="30.00",
                   online=True, idempotency_key=key)
    assert tx2.pk == tx.pk


def account_balance_of(acct):
    from apps.ledger.services import account_balance

    return account_balance(acct.ledger_account)


@pytest.mark.django_db
def test_wrong_code_zero_movement(challenged_card, oob_capture):
    user, acct, card = challenged_card
    key = f"CD-{uuid4().hex[:10]}"
    err = _start_purchase(card, key)
    bal = account_balance_of(acct)
    journals = JournalEntry.objects.count()

    with pytest.raises(CardDeclined) as e:
        resume_purchase(card_id=card.pk, merchant="Book Store", amount="30.00",
                        facts=dict(err.facts), code="000000",
                        challenge_id=err.challenge_id, online=True)
    assert "INVALID_CODE" in str(e.value)
    assert RiskChallenge.objects.get(pk=err.challenge_id).status == RiskChallenge.Status.PENDING
    assert account_balance_of(acct) == bal
    assert JournalEntry.objects.count() == journals
    from apps.cards.models import CardTransaction

    assert not CardTransaction.objects.filter(card=card, declined=False).exists()


@pytest.mark.django_db
def test_tampered_merchant_invalidate_zero_movement(challenged_card, oob_capture):
    """Changing a material fact (merchant) after issuance kills the challenge."""
    user, acct, card = challenged_card
    key = f"CD-{uuid4().hex[:10]}"
    err = _start_purchase(card, key)
    tampered = {**err.facts, "merchant": "Evil Shop"}

    with pytest.raises(CardDeclined) as e:
        resume_purchase(card_id=card.pk, merchant="Evil Shop", amount="30.00",
                        facts=tampered, code=_code_of(oob_capture, err.challenge_id),
                        challenge_id=err.challenge_id, online=True)
    assert "MATERIAL_CHANGED" in str(e.value)
    assert RiskChallenge.objects.get(pk=err.challenge_id).status == RiskChallenge.Status.EXPIRED
    assert not JournalEntry.objects.filter(description__contains="Evil Shop").exists()


@pytest.mark.django_db
def test_confirmed_challenge_cannot_bypass_fresh_block(challenged_card, oob_capture):
    """A valid code presented while the engine now says ENFORCEMENT BLOCK still declines."""
    user, acct, card = challenged_card
    from django.contrib.auth import get_user_model

    mgr = get_user_model().objects.get(username="cd-mgr")
    key = f"CD-{uuid4().hex[:10]}"
    err = _start_purchase(card, key)
    code = _code_of(oob_capture, err.challenge_id)

    modes.set_mode(RiskEvaluation.EngineMode.ENFORCEMENT, actor=mgr)
    with pytest.raises(CardDeclined) as e:
        resume_purchase(card_id=card.pk, merchant="Book Store", amount="30.00",
                        facts=dict(err.facts), code=code,
                        challenge_id=err.challenge_id, online=True)
    assert e.value.args[0] == "RISK_BLOCKED"
    from apps.cards.models import CardTransaction

    assert not CardTransaction.objects.filter(card=card, declined=False).exists()
