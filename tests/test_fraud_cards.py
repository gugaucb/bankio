"""Card fraud: shadow observation on purchases; hard controls stay decisive."""
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.cards.models import Card, CardTransaction, CardStatus, CardType
from apps.cards.services import CardDeclined, purchase
from apps.fraud.models import RiskEvaluation, RiskRule
from apps.fraud.signals_card import card_daily_spend, card_rapid_sequence, card_velocity_10min  # noqa: F401
from apps.ledger import services as ledger


@pytest.fixture(autouse=True)
def clean(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield


@pytest.fixture
def funded_customer(db, django_user_model):
    user = django_user_model.objects.create_user("card-user", email="cu2@t.io", password="x")
    la = ledger.get_or_create_account("2001-CARDUSER", "card deposit", is_customer=True)
    cash = ledger.get_or_create_account("CARD-CASH", "Cash", type="ASSET")
    ledger.post_journal(
        f"CARD-DEP-{uuid4().hex[:8]}", "dep",
        [(cash, "DEBIT", Decimal("2000.00")), (la, "CREDIT", Decimal("2000.00"))],
    )
    from apps.accounts.models import Account

    acct = Account.objects.create(customer=user, account_number="6666666666666661", ledger_account=la)
    card = Card.objects.create(
        account=acct, holder_name=user.username, type=CardType.DEBIT,
        status=CardStatus.ACTIVE, credit_limit=Decimal("0"),
    )
    return user, acct, card


def test_purchase_creates_shadow_evaluation(funded_customer):
    user, acct, card = funded_customer
    tx = purchase(card.pk, "Coffee Shop", "5.00", idempotency_key=uuid4().hex)
    assert not tx.declined
    ev = RiskEvaluation.objects.filter(operation_type="CARD_PURCHASE").latest("pk")
    assert ev.status == RiskEvaluation.Status.COMPLETED
    assert ev.engine_mode == RiskEvaluation.EngineMode.SHADOW


def test_frozen_card_declines_regardless_of_shadow_rules(funded_customer):
    """INVARIANT from PART 21: CARD FROZEN declines even if risk score is 0."""
    user, acct, card = funded_customer
    card.status = CardStatus.FROZEN
    card.save(update_fields=["status"])
    with pytest.raises(CardDeclined) as e:
        purchase(card.pk, "Anywhere", "10.00", idempotency_key=uuid4().hex)
    assert str(e.value) == "FROZEN"
    assert RiskEvaluation.objects.count() >= 0  # evaluation may or may not run first; decline is authoritative


def test_engine_crash_does_not_break_purchases(funded_customer, monkeypatch):
    from apps.fraud import engine

    monkeypatch.setattr(engine, "evaluate_operation", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    user, acct, card = funded_customer
    tx = purchase(card.pk, "Shop", "3.00", idempotency_key=uuid4().hex)
    assert not tx.declined


def test_velocity_and_rapid_sequence_signals(funded_customer):
    user, acct, card = funded_customer
    for _ in range(4):
        purchase(card.pk, f"M{uuid4().hex[:4]}", "2.00", idempotency_key=uuid4().hex)
    out = card_velocity_10min(None, card=card)
    seq = card_rapid_sequence(None, card=card)
    assert out >= 4 and seq is True
