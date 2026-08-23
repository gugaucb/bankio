"""Signal registry: facts are deterministic, isolated, and history-aware."""
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.fraud import signals
from apps.fraud.context import RiskContext


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user("sig-user", password="x")


def _ctx(user=None, **kw):
    base = dict(operation_type="TRANSFER", timestamp=timezone.now())
    if user:
        base["actor"] = user
    base.update(kw)
    return RiskContext(**base)


def test_amount_and_time_signals_are_pure_facts(user):
    ctx = _ctx(user, amount=Decimal("123.45"))
    out = signals.collect(ctx, ["TRANSACTION_AMOUNT", "TIME_OF_DAY_HOUR"])
    assert out["TRANSACTION_AMOUNT"] == "123.45"
    assert out["TIME_OF_DAY_HOUR"] == ctx.timestamp.hour


def test_new_device_true_when_untracked_and_false_when_trusted(db, user):
    from apps.identity.models import Device

    ctx = _ctx(user, device_id="d" * 64)
    assert signals.collect(ctx, ["NEW_DEVICE"])["NEW_DEVICE"] is True

    Device.objects.create(user=user, device_id="d" * 64, trusted=True)
    assert signals.collect(ctx, ["NEW_DEVICE"])["NEW_DEVICE"] is False


def test_failed_login_count_counts_only_last_24h(db, user):
    AuditLog.objects.create(actor=user, action="LOGIN_FAILED")
    old = AuditLog.objects.create(actor=user, action="LOGIN_FAILED")
    AuditLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timezone.timedelta(hours=30))
    out = signals.collect(_ctx(user), ["FAILED_LOGIN_COUNT_24H"])
    assert out["FAILED_LOGIN_COUNT_24H"] == 1


def test_signal_error_is_isolated_not_fatal(user):
    ctx = _ctx(user, amount=Decimal("1"))
    out = signals.collect(ctx, ["ACCOUNT_AGE_DAYS", "TRANSACTION_AMOUNT"])  # no account supplied
    assert out["ACCOUNT_AGE_DAYS"] is None
    assert out["TRANSACTION_AMOUNT"] == "1"

    # a broken signal must not break the batch
    def boom(ctx):
        raise RuntimeError("boom")

    signals.REGISTRY["_BROKEN_TEST"] = boom
    try:
        out = signals.collect(ctx, ["_BROKEN_TEST", "TRANSACTION_AMOUNT"])
        assert "__error__" in out["_BROKEN_TEST"]
        assert out["TRANSACTION_AMOUNT"] == "1"
    finally:
        del signals.REGISTRY["_BROKEN_TEST"]


def test_unknown_signal_reported_not_raised():
    out = signals.collect(_ctx(), ["NOPE_SIGNAL"])
    assert out["NOPE_SIGNAL"] == {"__error__": "unknown signal"}
