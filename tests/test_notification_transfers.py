"""FASE 6 Branch 3 — transfer notification integration tests."""
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.notifications.models import Notification

D = Decimal


def _user(username):
    from tests.conftest import make_user
    from apps.customers.models import Customer
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _rule(score, rule_id):
    from apps.fraud.models import RiskRule
    return RiskRule.objects.create(rule_id=rule_id, name="n", score=score,
                                   lifecycle=RiskRule.Lifecycle.ACTIVE,
                                   enabled=True, operation_types=["TRANSFER"])


def _account(user, balance="1000.00"):
    from apps.accounts.models import Account
    from apps.ledger.services import get_or_create_account, post_journal
    la = get_or_create_account(f"2001-NT-{user.username}", f"A {user.username}", is_customer=True)
    a = Account.objects.create(customer=user, account_number=f"11{user.pk:010d}", ledger_account=la)
    if Decimal(str(balance)) > 0:
        equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        post_journal(reference=f"OPEN-NT-{a.pk}", description="opening",
                     lines=[(equity, "DEBIT", D(str(balance))), (la, "CREDIT", D(str(balance)))])
    return a


@pytest.mark.django_db(transaction=True)
class TestTransferNotifications:
    def _transfer(self, sa, ra, key):
        from apps.transfers.services import execute_transfer
        t, created = execute_transfer(actor=sa.customer, source_account_id=sa.pk,
                                      amount=D("25.00"),
                                      destination_account_id=ra.pk,
                                      idempotency_key=key)
        return t, created

    def test_completed_and_received_exactly_once(self, db):
        from django.test.utils import CaptureQueriesContext  # noqa: F401
        s = _user("nt-a"); sa = _account(s)
        r = _user("nt-b"); ra = _account(r, "50.00")
        t, _ = self._transfer(sa, ra, f"NT-{sa.pk}-{ra.pk}")
        # flush on_commit callbacks (test runner wraps in atomic)
        t.refresh_from_db()
        assert t.status == "COMPLETED"
        kinds = list(Notification.objects.order_by("kind").values_list("kind", flat=True))
        assert kinds.count("TRANSFER_COMPLETED") == 1
        assert kinds.count("TRANSFER_RECEIVED") == 1
        completed = Notification.objects.get(kind="TRANSFER_COMPLETED")
        received = Notification.objects.get(kind="TRANSFER_RECEIVED")
        assert completed.recipient_id == s.pk and received.recipient_id == r.pk

    def test_replay_creates_no_new_notifications(self, db):
        from apps.transfers.services import execute_transfer
        s = _user("nt-c"); sa = _account(s)
        r = _user("nt-d"); ra = _account(r, "50.00")
        key = f"NT-RP-{s.pk}"
        self._transfer(sa, ra, key)
        before = Notification.objects.count()
        execute_transfer(actor=s, source_account_id=sa.pk, amount=D("25.00"),
                         destination_account_id=ra.pk, idempotency_key=key)
        assert Notification.objects.count() == before

    def test_risk_block_notifies_failed_never_success(self, db, settings):
        settings.FRAUD_MODE = "ENFORCEMENT"
        from apps.fraud.models import FraudEngineSetting, RiskRule
        from apps.fraud.modes import set_mode
        from tests.conftest import make_user
        mgr = make_user("nt-mgr", role="FRAUD_MANAGER")
        set_mode("ENFORCEMENT", actor=mgr)
        RiskRule.objects.all().delete()
        _rule(95, "NT-BLOCK-R")
        s = _user("nt-e"); sa = _account(s)
        r = _user("nt-f"); ra = _account(r, "50.00")
        from apps.transfers.services import execute_transfer, TransferError
        with pytest.raises(TransferError):
            execute_transfer(actor=s, source_account_id=sa.pk, amount=D("10.00"),
                             destination_account_id=ra.pk,
                             idempotency_key=f"NT-BLOCK-{s.pk}")
        notes = list(Notification.objects.filter(recipient=s))
        assert [n.kind for n in notes] == ["TRANSFER_FAILED"]
        body = notes[0].body.lower()
        assert "completed" not in body and "success" not in body and "received" not in body
        # no fraud internals leaked
        raw = str(notes[0].__dict__) + notes[0].body + notes[0].title
        assert "score" not in raw.lower() and "rule" not in raw.lower()

    def test_under_review_is_pending_not_success(self, db, settings):
        settings.FRAUD_MODE = "ENFORCEMENT"
        from apps.fraud.models import RiskRule
        from apps.fraud.modes import set_mode
        from tests.conftest import make_user
        mgr = make_user("nt-mgr2", role="FRAUD_MANAGER")
        set_mode("ENFORCEMENT", actor=mgr)
        RiskRule.objects.all().delete()
        _rule(70, "NT-REVIEW-R")
        s = _user("nt-g"); sa = _account(s)
        r = _user("nt-h"); ra = _account(r, "50.00")
        from apps.transfers.services import execute_transfer
        execute_transfer(actor=s, source_account_id=sa.pk, amount=D("10.00"),
                         destination_account_id=ra.pk,
                         idempotency_key=f"NT-REV-{s.pk}")
        note = Notification.objects.get(recipient=s, kind="TRANSFER_UNDER_REVIEW")
        assert "review" in note.body.lower()
        assert not Notification.objects.filter(kind__contains="COMPLETED",
                                               recipient=s).exists()

    def test_reversal_new_event_original_kept(self, db):
        from apps.transfers.services import execute_transfer, reverse_transfer
        from apps.transfers.models import Transfer
        s = _user("nt-i"); sa = _account(s)
        r = _user("nt-j"); ra = _account(r, "50.00")
        t, _ = self._transfer(sa, ra, f"NT-RV-{s.pk}")
        reverse_transfer(Transfer.objects.get(pk=t.pk), actor=s)
        sender_notes = list(Notification.objects.filter(recipient=s).order_by("id"))
        kinds = [n.kind for n in sender_notes]
        assert kinds == ["TRANSFER_COMPLETED", "TRANSFER_REVERSED"]

    def test_notification_failure_does_not_break_settlement(self, db, monkeypatch):
        from apps.transfers.services import execute_transfer
        import apps.notifications.services as nsvc
        s = _user("nt-k"); sa = _account(s)
        r = _user("nt-l"); ra = _account(r, "50.00")
        monkeypatch.setattr(nsvc, "_create",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        t, _ = execute_transfer(actor=s, source_account_id=sa.pk, amount=D("5.00"),
                                destination_account_id=ra.pk,
                                idempotency_key=f"NT-Fail-{s.pk}")
        t.refresh_from_db()
        assert t.status == "COMPLETED"          # settlement unaffected
        assert float(sa.current_balance) == 995.00
        assert AuditLog_has_error()

    def test_ledger_snapshot_unchanged_by_reading_notifications(self, db, client):
        from apps.ledger.models import JournalEntry
        s = _user("nt-m"); sa = _account(s)
        r = _user("nt-n"); ra = _account(r, "50.00")
        self._transfer(sa, ra, f"NT-SNAP-{s.pk}")
        snap = list(JournalEntry.objects.values_list("id", "chain_hash"))
        balance = float(sa.current_balance)
        client.force_login(s)
        client.get(reverse("app_notifications"))
        client.post(reverse("app_notification_read",
                            args=[Notification.objects.first().pk]))
        assert list(JournalEntry.objects.values_list("id", "chain_hash")) == snap
        assert float(sa.current_balance) == balance


def AuditLog_has_error():
    from apps.audit.models import AuditLog
    return AuditLog.objects.filter(action="NOTIFICATION_ERROR").exists()
