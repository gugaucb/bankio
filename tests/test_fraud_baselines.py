"""Behavior baselines: reliable only above the minimum sample threshold (§57)."""
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.fraud.baselines import (
    MIN_OBSERVATIONS,
    amount_multiplier_vs_baseline,
    is_unusual_hour,
    transfer_baseline,
)
from apps.ledger import services as ledger
from apps.transfers.models import Transfer, TransferStatus


@pytest.fixture
def setup(db, django_user_model):
    user = django_user_model.objects.create_user("bl-user", email="bl@t.io", password="x")
    la = ledger.get_or_create_account("2001-BL", "BL deposit", is_customer=True)
    from apps.accounts.models import Account

    acct = Account.objects.create(customer=user, account_number="8888888888888881", ledger_account=la)
    return user, acct


def _transfer(acct, amount, hours_ago=0):
    from django.utils import timezone

    t = Transfer.objects.create(
        reference=f"BL-{uuid4().hex[:10]}",
        created_by=acct.customer,
        source_account=acct,
        amount=Decimal(amount),
        currency="USD",
        status=TransferStatus.COMPLETED,
        idempotency_key=f"bl-{uuid4().hex}",
    )
    if hours_ago:
        Transfer.objects.filter(pk=t.pk).update(created_at=timezone.now() - timezone.timedelta(hours=hours_ago))
    return t


def test_small_sample_flagged_unreliable_not_fabricated(setup):
    user, acct = setup
    for _ in range(MIN_OBSERVATIONS - 1):
        _transfer(acct, "100.00")
    base = transfer_baseline(user)
    assert base["sample_size"] == MIN_OBSERVATIONS - 1
    assert base["reliable"] is False
    # no fabricated multiplier without a reliable baseline
    assert amount_multiplier_vs_baseline(user, "1000.00") is None


def test_reliable_baseline_at_threshold_boundary(setup):
    """Boundary: exactly MIN_OBSERVATIONS flips reliability."""
    user, acct = setup
    for i in range(MIN_OBSERVATIONS - 1):
        _transfer(acct, "100.00", hours_ago=i + 2)
    assert transfer_baseline(user)["reliable"] is False
    _transfer(acct, "100.00")
    base = transfer_baseline(user)
    assert base["reliable"] is True and base["sample_size"] == MIN_OBSERVATIONS
    assert Decimal(base["avg_amount"]) == Decimal("100.00")
    assert Decimal(base["largest_normal_transfer"]) == Decimal("100.00")


def test_multiplier_and_unusual_hour(setup):
    user, acct = setup
    for i in range(6):  # daytime transfers
        _transfer(acct, "100.00", hours_ago=24 + i * 5)
    mult = amount_multiplier_vs_baseline(user, "1000.00")
    assert mult == 10.0
    assert isinstance(is_unusual_hour(user, 3), bool)


def test_empty_customer_is_neutral(setup):
    user, _ = setup
    base = transfer_baseline(user)
    assert base["reliable"] is False and base["sample_size"] == 0
