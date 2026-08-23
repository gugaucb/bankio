"""Velocity signals: window boundaries (4/5/6), aggregation, isolation."""
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Account, AccountStatus, Beneficiary
from apps.fraud import signals, signals_velocity  # noqa: F401  (registration)
from apps.fraud.context import RiskContext
from apps.transfers.models import Transfer, TransferStatus


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user("vel-user", email="vel-user@t.io", password="x")


@pytest.fixture
def account(db, user):
    from apps.accounts.models import Account

    la = __import__("apps.ledger.services", fromlist=["get_or_create_account"]).get_or_create_account(
        f"VEL-{user.username}", "Vel", is_customer=True
    )
    return Account.objects.create(
        customer=user, account_number="9999999999999999", ledger_account=la,
    )


def _ctx(user):
    return RiskContext(operation_type="TRANSFER", actor=user, timestamp=timezone.now())


def _mk_transfer(account, amount="10.00", minutes_ago=0, status=TransferStatus.COMPLETED):
    t = Transfer.objects.create(
        reference=f"VEL-{timezone.now().timestamp()}-{amount}-{minutes_ago}",
        created_by=account.customer,
        idempotency_key=f"vel-{timezone.now().timestamp()}-{amount}-{minutes_ago}",
        source_account=account,
        destination_account=None,
        beneficiary=None,
        amount=Decimal(amount),
        currency="USD",
        status=status,
    )
    if minutes_ago:
        Transfer.objects.filter(pk=t.pk).update(created_at=timezone.now() - timezone.timedelta(minutes=minutes_ago))
    return t


def test_velocity_window_boundaries_4_5_6(user, account):
    ctx = _ctx(user)
    for _ in range(4):
        _mk_transfer(account)
    assert signals.collect(ctx, ["TRANSFER_VELOCITY_10MIN"], source_account=account)["TRANSFER_VELOCITY_10MIN"] == 4
    _mk_transfer(account)
    assert signals.collect(ctx, ["TRANSFER_VELOCITY_10MIN"], source_account=account)["TRANSFER_VELOCITY_10MIN"] == 5
    _mk_transfer(account)
    assert signals.collect(ctx, ["TRANSFER_VELOCITY_10MIN"], source_account=account)["TRANSFER_VELOCITY_10MIN"] == 6


def test_old_transfers_do_not_count_in_10min_window(user, account):
    _mk_transfer(account, minutes_ago=11)
    _mk_transfer(account, minutes_ago=9)
    out = signals.collect(_ctx(user), ["TRANSFER_VELOCITY_10MIN"], source_account=account)
    assert out["TRANSFER_VELOCITY_10MIN"] == 1


def test_failed_transfers_excluded_from_velocity(user, account):
    _mk_transfer(account, status=TransferStatus.FAILED)
    _mk_transfer(account)
    out = signals.collect(_ctx(user), ["TRANSFER_VELOCITY_1H"], source_account=account)
    assert out["TRANSFER_VELOCITY_1H"] == 1


def test_hourly_and_daily_totals_aggregate_amounts(user, account):
    _mk_transfer(account, amount="100.00")
    _mk_transfer(account, amount="50.00")
    out = signals.collect(
        _ctx(user), ["TRANSFER_TOTAL_1H", "DAILY_TRANSFER_TOTAL"], source_account=account
    )
    assert Decimal(out["TRANSFER_TOTAL_1H"]) == Decimal("150.00")
    assert Decimal(out["DAILY_TRANSFER_TOTAL"]) == Decimal("150.00")


def test_new_beneficiaries_count_per_owner(user, account, django_user_model):
    other = django_user_model.objects.create_user("vel-other", email="vel-other@t.io", password="x")
    Beneficiary.objects.create(owner=user, name="a", account_number="111")
    Beneficiary.objects.create(owner=user, name="b", account_number="222")
    Beneficiary.objects.create(owner=other, name="c", account_number="333")
    out = signals.collect(_ctx(user), ["NEW_BENEFICIARIES_24H"])
    assert out["NEW_BENEFICIARIES_24H"] == 2


def test_missing_source_account_returns_none_not_error(user):
    out = signals.collect(_ctx(user), ["TRANSFER_VELOCITY_10MIN"])
    assert out["TRANSFER_VELOCITY_10MIN"] is None
