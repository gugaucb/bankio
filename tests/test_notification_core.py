"""FASE 6 Branch 1 — notification core service tests."""
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.db import transaction as django_tx

from apps.audit.models import AuditLog
from apps.notifications.models import Category, Notification
from apps.notifications.services import (
    MANDATORY_NOTIFICATION_KINDS,
    NotificationError,
    mark_all_read,
    mark_read,
    notify,
)

D = Decimal


def _user(username):
    from tests.conftest import make_user
    from apps.customers.models import Customer
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


@pytest.mark.django_db
class TestNotificationCore:
    def test_basic_create(self):
        u = _user("nc-a")
        n = notify(recipient=u, category="TRANSFER", kind="X_TEST",
                   title="t", body="b", dedup_key="K1")
        assert n is not None and n.recipient_id == u.pk and n.kind == "X_TEST"

    def test_dedup_same_key_returns_existing(self):
        u = _user("nc-b")
        a = notify(recipient=u, category="SYSTEM", title="1", dedup_key="DUP-1")
        b = notify(recipient=u, category="SYSTEM", title="2", dedup_key="DUP-1")
        assert a.pk == b.pk
        assert Notification.objects.count() == 1

    def test_different_events_different_notifications(self):
        u = _user("nc-c")
        notify(recipient=u, category="SYSTEM", title="1", dedup_key="E1")
        notify(recipient=u, category="SYSTEM", title="2", dedup_key="E2")
        assert Notification.objects.count() == 2

    def test_different_recipients_different_notifications(self):
        a = _user("nc-d"); b = _user("nc-e")
        notify(recipient=a, category="SYSTEM", title="x")
        notify(recipient=b, category="SYSTEM", title="x")
        assert Notification.objects.count() == 2

    def test_dedup_unique_constraint_backed(self):
        """The DB constraint is the concurrency guard; the service recovers."""
        u = _user("nc-f")
        Notification.objects.create(recipient=u, category=Category.SYSTEM,
                                    title="first", dedup_key=f"RACE:{u.pk}")
        with pytest.raises(IntegrityError):
            with django_tx.atomic():
                Notification.objects.create(recipient=u, category=Category.SYSTEM,
                                            title="second", dedup_key=f"RACE:{u.pk}")
        assert Notification.objects.filter(dedup_key=f"RACE:{u.pk}").count() == 1
        # service path converts the same race into an idempotent replay
        got = notify(recipient=u, category="SYSTEM", title="third",
                     dedup_key=f"RACE:{u.pk}")
        assert got.title == "first"
        assert Notification.objects.filter(dedup_key=f"RACE:{u.pk}").count() == 1

    def test_service_recovers_integrity_error_to_existing_row(self, monkeypatch):
        from django.db.models.manager import Manager
        u = _user("nc-g")
        first = notify(recipient=u, category="SYSTEM", title="a", dedup_key="R1")
        real_create = Notification.objects.create

        def failing_create(*a, **kw):
            raise IntegrityError("duplicate")

        monkeypatch.setattr(Notification.objects, "create", failing_create)
        second = notify(recipient=u, category="SYSTEM", title="b", dedup_key="R1")
        assert second is not None and second.pk == first.pk

    def test_invalid_category_and_kind_audited_no_raise(self):
        u = _user("nc-h")
        assert notify(recipient=u, category="NOPE", title="x") is None
        assert notify(recipient=u, category="SYSTEM", title="x", kind="bad kind!") is None
        actions = list(AuditLog.objects.filter(action="NOTIFICATION_ERROR")
                       .values_list("metadata", flat=True))
        assert len(actions) == 2
        assert all("exception" in m for m in actions)

    def test_oversized_metadata_rejected_audited(self):
        u = _user("nc-i")
        assert notify(recipient=u, category="SYSTEM", title="x",
                      metadata={"k": "v" * 500}) is None
        assert AuditLog.objects.filter(action="NOTIFICATION_ERROR").exists()

    def test_failure_does_not_propagate_to_financial_caller(self, monkeypatch):
        from apps.notifications import services as svc
        u = _user("nc-j")
        monkeypatch.setattr(svc, "_create", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        result = svc.notify(recipient=u, category="SYSTEM", title="x", dedup_key="Z")
        assert result is None  # caller (settlement) continues unharmed
        assert AuditLog.objects.filter(action="NOTIFICATION_ERROR").exists()

    def test_payload_rejects_unsupported_types(self):
        u = _user("nc-k")
        assert notify(recipient=u, category="SECURITY", title="x",
                      metadata={"obj": object()}) is None
        assert notify(recipient=u, category="SECURITY", title="y",
                      metadata={"nested": {"a": 1}}) is None
        # bounded strings are fine
        assert notify(recipient=u, category="SECURITY", title="z",
                      metadata={"operation": "TRANSFER"}) is not None

    def test_challenge_notification_still_works_via_service(self):
        from decimal import Decimal as Dc
        from apps.fraud.challenge_delivery import issue_and_deliver
        from apps.fraud.models import RiskEvaluation
        u = _user("nc-l")
        FACTS = {"amount": "10.00", "beneficiary": "7",
                 "idempotency_key": f"k-nc-{u.pk}"}
        ev = RiskEvaluation.objects.create(
            operation_type="TRANSFER", customer=u, actor=u,
            amount=Dc("10.00"), currency="USD", engine_mode="CHALLENGE_ONLY",
            status=RiskEvaluation.Status.COMPLETED,
            decision=RiskEvaluation.Decision.BLOCK,
            idempotency_key=FACTS["idempotency_key"])
        ch, code = issue_and_deliver(ev, u, FACTS)
        note = Notification.objects.get(recipient=u, category="SECURITY")
        assert code not in note.body and note.kind == "CHALLENGE_ISSUED"
        assert note.dedup_key.startswith("CHALLENGE_ISSUED:")

    def test_mark_read_sets_read_at_idempotent(self):
        u = _user("nc-m")
        n = notify(recipient=u, category="SYSTEM", title="r", dedup_key="MR1")
        mark_read(n)
        first_ts = n.read_at
        assert n.read and first_ts is not None
        mark_read(n)
        assert n.read_at == first_ts  # idempotent

    def test_mark_all_read(self):
        u = _user("nc-n")
        for i in range(3):
            notify(recipient=u, category="SYSTEM", title=str(i), dedup_key=f"M{i}")
        assert mark_all_read(u) == 3
        assert u.notifications.filter(read=False).count() == 0

    def test_mandatory_kinds_whitelist_exists(self):
        assert "PASSWORD_CHANGED" in MANDATORY_NOTIFICATION_KINDS
        assert "CHALLENGE_ISSUED" in MANDATORY_NOTIFICATION_KINDS

    def test_legacy_rows_without_kind_still_orderable(self):
        u = _user("nc-o")
        Notification.objects.create(recipient=u, category="LEGACY?", title="old")
        assert u.notifications.order_by("-created_at", "-id").first().title == "old"
