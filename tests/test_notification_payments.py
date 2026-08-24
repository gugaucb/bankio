"""FASE 6 Branch 4 — payment notification integration tests."""
from decimal import Decimal

import pytest

from apps.notifications.models import Notification
from apps.payments.models import Bill

D = Decimal


def _user(username):
    from tests.conftest import make_user
    from apps.customers.models import Customer
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _account(user, balance="1000.00"):
    from apps.accounts.models import Account
    from apps.ledger.services import get_or_create_account, post_journal
    la = get_or_create_account(f"2001-NP-{user.username}", f"A {user.username}", is_customer=True)
    a = Account.objects.create(customer=user, account_number=f"99{user.pk:010d}", ledger_account=la)
    if Decimal(str(balance)) > 0:
        equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        post_journal(reference=f"OPEN-NP-{a.pk}", description="opening",
                     lines=[(equity, "DEBIT", D(str(balance))), (la, "CREDIT", D(str(balance)))])
    return a


@pytest.mark.django_db(transaction=True)
class TestPaymentNotifications:
    def _pay(self, user, acct, key):
        from apps.payments.services import pay_bill
        bill = Bill.objects.create(biller="NP Power Co", amount=D("40.00"))
        return pay_bill(actor=user, account_id=acct.pk, bill_id=bill.pk,
                        idempotency_key=key)

    def test_completed_exactly_once(self):
        s = _user("np-a"); a = _account(s)
        p, created = self._pay(s, a, f"NP-{s.pk}")
        assert created and p.status == "COMPLETED"
        note = Notification.objects.get(recipient=s, kind="PAYMENT_COMPLETED")
        assert "completed" in note.body and str(p.journal.reference) in note.body

    def test_idempotency_replay_no_new_notification(self):
        from apps.payments.models import Payment
        from apps.payments.services import pay_bill
        s = _user("np-b"); a = _account(s)
        key = f"NP-RP-{s.pk}"
        self._pay(s, a, key)
        before = Notification.objects.count()
        bill2 = Bill.objects.create(biller="NP Power Co", amount=D("40.00"))
        p2, created = pay_bill(actor=s, account_id=a.pk, bill_id=bill2.pk,
                               idempotency_key=key)
        assert not created and p2.pk == Payment.objects.get(idempotency_key=key).pk
        # replay: no new notification, no second settlement, ledger untouched
        assert Notification.objects.count() == before
        assert float(a.current_balance) == 960.00

    def test_insufficient_funds_no_success_notification(self):
        from apps.payments.services import pay_bill, PaymentError
        s = _user("np-c"); a = _account(s, "5.00")
        with pytest.raises(PaymentError):
            self._pay(s, a, f"NP-POOR-{s.pk}")
        assert not Notification.objects.filter(recipient=s).exists()

    def test_notification_failure_does_not_break_payment(self, monkeypatch):
        import apps.notifications.services as nsvc
        from apps.payments.services import pay_bill
        s = _user("np-d"); a = _account(s)
        monkeypatch.setattr(nsvc, "_create",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        p, created = self._pay(s, a, f"NP-Fail-{s.pk}")
        assert created and p.status == "COMPLETED"
        balance_after = float(a.current_balance)
        assert balance_after == 960.00  # 1000 - 40 settled regardless of notification
        from apps.audit.models import AuditLog
        assert AuditLog.objects.filter(action="NOTIFICATION_ERROR").exists()

    def test_ledger_snapshot_unchanged_by_notifications(self):
        from apps.ledger.models import JournalEntry
        s = _user("np-e"); a = _account(s)
        self._pay(s, a, f"NP-SNAP-{s.pk}")
        snap = list(JournalEntry.objects.values_list("id", "chain_hash"))
        bal = float(a.current_balance)
        n = Notification.objects.get(recipient=s, kind="PAYMENT_COMPLETED")
        n.read = True; n.save(update_fields=["read"])
        assert list(JournalEntry.objects.values_list("id", "chain_hash")) == snap
        assert float(a.current_balance) == bal
