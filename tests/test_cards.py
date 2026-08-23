"""Card domain: freeze/controls, purchase declines, statement payments."""
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied

from apps.cards.models import Card, CardStatus, CreditStatement
from apps.cards.services import (
    CardDeclined,
    freeze_card,
    pay_statement,
    purchase,
    set_card_control,
    unfreeze_card,
)


@pytest.fixture
def debit_card(alice):
    return Card.objects.create(account=alice.checking, type="DEBIT_CARD", holder_name="Alice")


def test_purchase_posts_ledger(alice, debit_card):
    tx = purchase(debit_card.pk, "Whole Foods", "50.00")
    assert not tx.declined
    assert alice.checking.current_balance == Decimal("950.00")


def test_frozen_card_declines(alice, debit_card):
    freeze_card(alice, debit_card.pk)
    with pytest.raises(CardDeclined) as e:
        purchase(debit_card.pk, "Store", "10.00")
    assert e.value.reason == "FROZEN"
    unfreeze_card(alice, debit_card.pk)
    tx = purchase(debit_card.pk, "Store", "10.00")
    assert not tx.declined


def test_insufficient_funds_card_decline(alice, debit_card):
    # below tx_limit (2000) but above balance (1000)
    with pytest.raises(CardDeclined) as e:
        purchase(debit_card.pk, "Lux", "1500.00")
    assert e.value.reason == "INSUFFICIENT_FUNDS"


def test_tx_limit_and_daily_limit(alice, debit_card):
    set_card_control(alice, debit_card.pk, tx_limit=Decimal("100.00"))
    with pytest.raises(CardDeclined) as e:
        purchase(debit_card.pk, "Big", "200.00")
    assert e.value.reason == "TX_LIMIT_EXCEEDED"
    set_card_control(alice, debit_card.pk, daily_limit=Decimal("50.00"), tx_limit=Decimal("1000.00"))
    with pytest.raises(CardDeclined) as e:
        purchase(debit_card.pk, "Mid", "60.00")
    assert e.value.reason == "DAILY_LIMIT_EXCEEDED"


def test_expired_card(alice, debit_card):
    from datetime import date

    debit_card.expiry_month = 1
    debit_card.expiry_year = 2020
    debit_card.save()
    with pytest.raises(CardDeclined) as e:
        purchase(debit_card.pk, "Old", "5.00")
    assert e.value.reason == "EXPIRED"


def test_online_disabled(alice, debit_card):
    set_card_control(alice, debit_card.pk, online_enabled=False)
    with pytest.raises(CardDeclined) as e:
        purchase(debit_card.pk, "Webshop", "20.00", online=True)
    assert e.value.reason == "ONLINE_DISABLED"


def test_international_disabled_by_default(alice, debit_card):
    with pytest.raises(CardDeclined) as e:
        purchase(debit_card.pk, "Abroad", "20.00", international=True)
    assert e.value.reason == "INTERNATIONAL_DISABLED"


def test_other_user_cannot_control_card(bob, alice, debit_card):
    with pytest.raises(PermissionDenied):
        freeze_card(bob, debit_card.pk)


def test_masked_pan_only(alice, debit_card):
    assert debit_card.masked_number.startswith("•••• •••• •••• ")
    # no full PAN field exists on the model
    assert not hasattr(debit_card, "pan") and not hasattr(debit_card, "number")


def test_statement_payment(alice, bob, account_factory, client=None):
    card = Card.objects.create(account=alice.checking, type="CREDIT_CARD",
                               holder_name="Alice", credit_limit="2000.00")
    from datetime import date

    stmt = CreditStatement.objects.create(card=card, period_start=date(2026, 7, 1),
                                          period_end=date(2026, 7, 31), amount_due="300.00")
    total = pay_statement(alice, card.pk)
    assert total == Decimal("300.00")
    stmt.refresh_from_db()
    assert stmt.paid and alice.checking.current_balance == Decimal("700.00")
    with pytest.raises(CardDeclined):
        pay_statement(alice, card.pk)  # duplicate payment impossible


def test_credit_limit_enforced(alice):
    card = Card.objects.create(account=alice.checking, type="CREDIT_CARD",
                               holder_name="Alice", credit_limit="100.00")
    with pytest.raises(CardDeclined) as e:
        purchase(card.pk, "Luxury", "500.00")
    assert e.value.reason == "CREDIT_LIMIT_EXCEEDED"
