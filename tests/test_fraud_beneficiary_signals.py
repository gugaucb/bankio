"""Beneficiary signals: age boundaries, first-transfer, recent activity."""
from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.accounts.models import Beneficiary
from apps.fraud import signals  # noqa: F401
from apps.fraud import signals_beneficiary  # noqa: F401 (registration)
from apps.fraud.context import RiskContext
from apps.transfers.models import Transfer, TransferStatus


@pytest.fixture
def setup(db, django_user_model):
    user = django_user_model.objects.create_user("ben-user", email="bn@t.io", password="x")
    la = __import__("apps.ledger.services", fromlist=["get_or_create_account"]).get_or_create_account(
        "2001-BEN", "Ben deposit", is_customer=True)
    from apps.accounts.models import Account

    acct = Account.objects.create(customer=user, account_number="7777777777777771", ledger_account=la)
    ben = Beneficiary.objects.create(owner=user, name="Drain Target", account_number="5550001")
    return user, acct, ben


def _ctx():
    return RiskContext(operation_type="TRANSFER", timestamp=timezone.now())


def test_missing_beneficiary_is_none_not_error(setup):
    out = signals.collect(_ctx(), ["BENEFICIARY_AGE_HOURS", "BENEFICIARY_IS_NEW"])
    assert out["BENEFICIARY_AGE_HOURS"] is None and out["BENEFICIARY_IS_NEW"] is None


def test_beneficiary_age_boundary_around_one_hour(setup):
    _, _, ben = setup
    now = timezone.now()
    for created, expected in ((now - timezone.timedelta(minutes=59), True),
                              (now - timezone.timedelta(minutes=61), False)):
        Beneficiary.objects.filter(pk=ben.pk).update(created_at=created)
        out = signals.collect(_ctx(), ["BENEFICIARY_IS_NEW"], beneficiary=Beneficiary.objects.get(pk=ben.pk))
        assert out["BENEFICIARY_IS_NEW"] is expected


def test_first_transfer_to_beneficiary(setup):
    user, acct, ben = setup

    def mk(beneficiary, minutes_ago=0):
        t = Transfer.objects.create(
            reference=f"BEN-{uuid4().hex[:8]}", created_by=user, source_account=acct,
            beneficiary=beneficiary, amount=Decimal("10.00"), currency="USD",
            status=TransferStatus.COMPLETED, idempotency_key=f"ben-{uuid4().hex}",
        )
        if minutes_ago:
            Transfer.objects.filter(pk=t.pk).update(
                created_at=timezone.now() - timezone.timedelta(minutes=minutes_ago))
        return t

    ctx = RiskContext(operation_type="TRANSFER",
                      timestamp=timezone.now() + timezone.timedelta(minutes=5))
    out = signals.collect(ctx, ["FIRST_TRANSFER_TO_BENEFICIARY"],
                          source_account=acct, beneficiary=ben)
    assert out["FIRST_TRANSFER_TO_BENEFICIARY"] is True  # no prior transfer yet

    mk(ben, minutes_ago=2)
    out = signals.collect(ctx, ["FIRST_TRANSFER_TO_BENEFICIARY"],
                          source_account=acct, beneficiary=ben)
    assert out["FIRST_TRANSFER_TO_BENEFICIARY"] is False


def test_recent_activity_aggregate(setup):
    user, acct, ben = setup
    t = Transfer.objects.create(
        reference=f"BEN-{uuid4().hex[:8]}", created_by=user, source_account=acct,
        beneficiary=ben, amount=Decimal("250.00"), currency="USD",
        status=TransferStatus.COMPLETED, idempotency_key=f"ben-{uuid4().hex}",
    )
    out = signals.collect(_ctx(), ["BENEFICIARY_TRANSFERS_24H"],
                          source_account=acct, beneficiary=ben)
    assert out["BENEFICIARY_TRANSFERS_24H"] == {"count": 1, "total": "250.00"}
