"""Device risk signals: trust, sharing, recency, failed-login history."""
import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.fraud import signals  # noqa: F401 (registry)
from apps.fraud.context import RiskContext
from apps.identity.models import Device


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user("dev-user", email="dv@t.io", password="x")


def _ctx(user=None, ip=""):
    return RiskContext(operation_type="TRANSFER", actor=user, device_id="d" * 64, ip=ip,
                       timestamp=timezone.now())


def test_device_last_seen_hours(user):
    Device.objects.create(user=user, device_id="d" * 64)
    out = signals.collect(_ctx(user), ["DEVICE_LAST_SEEN_HOURS"])
    assert out["DEVICE_LAST_SEEN_HOURS"] is not None
    assert out["DEVICE_LAST_SEEN_HOURS"] < 1


def test_unknown_device_returns_none_for_recency_but_true_for_new(user):
    out = signals.collect(_ctx(user), ["DEVICE_LAST_SEEN_HOURS", "NEW_DEVICE"])
    assert out["DEVICE_LAST_SEEN_HOURS"] is None
    assert out["NEW_DEVICE"] is True


def test_device_sharing_count_across_users(django_user_model, db, user):
    other = django_user_model.objects.create_user("dev-user2", email="dv2@t.io", password="x")
    third = django_user_model.objects.create_user("dev-user3", email="dv3@t.io", password="x")
    Device.objects.create(user=user, device_id="d" * 64)
    Device.objects.create(user=other, device_id="d" * 64)
    # third uses a different device
    Device.objects.create(user=third, device_id="e" * 64)
    ctx_third = RiskContext(operation_type="TRANSFER", actor=third, device_id="e" * 64,
                            timestamp=timezone.now())
    out = signals.collect(ctx_third, ["DEVICE_USER_COUNT"])
    assert out["DEVICE_USER_COUNT"] == 1
    out = signals.collect(_ctx(user), ["DEVICE_USER_COUNT"])
    assert out["DEVICE_USER_COUNT"] == 2


def test_failed_logins_from_same_ip_counted(user, db):
    for _ in range(3):
        AuditLog.objects.create(actor=user, action="LOGIN_FAILED", ip_address="203.0.113.9")
    AuditLog.objects.create(actor=user, action="LOGIN_FAILED", ip_address="198.51.100.1")
    out = signals.collect(_ctx(ip="203.0.113.9"), ["DEVICE_IP_FAILED_LOGINS_24H"])
    assert out["DEVICE_IP_FAILED_LOGINS_24H"] == 3


def test_no_ip_is_zero_not_error():
    out = signals.collect(RiskContext(operation_type="TRANSFER", timestamp=timezone.now()),
                          ["DEVICE_IP_FAILED_LOGINS_24H"])
    assert out["DEVICE_IP_FAILED_LOGINS_24H"] == 0
