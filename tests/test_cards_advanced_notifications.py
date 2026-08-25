"""FASE 8 Branch 8 — card/invoice lifecycle notification tests."""
from datetime import date
from decimal import Decimal

import pytest

from apps.notifications.models import Notification

D = Decimal


def _customer(username):
    from apps.customers.models import Customer
    from tests.conftest import make_user
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _card(username, **kw):
    from apps.accounts.models import Account
    from apps.cards.models import Card
    from apps.ledger.services import get_or_create_account
    u = _customer(username)
    la = get_or_create_account(f"2001-CN-{username}", f"A {username}", is_customer=True)
    a = Account.objects.create(customer=u, account_number=f"00{u.pk:010d}", ledger_account=la)
    return u, Card.objects.create(account=a, holder_name=username.upper(),
                                  type="CREDIT_CARD", credit_limit=D("500.00"), **kw)


@pytest.mark.django_db(transaction=True)
class TestLifecycleNotifications:
    def test_freeze_unfreeze_lost_notify_once(self, client):
        u, c = _card("cn-a")
        client.force_login(u)
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "freeze"})
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "freeze"})  # no-op
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "unfreeze"})
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "report_lost"})
        kinds = list(Notification.objects.filter(recipient=u).values_list("kind", flat=True))
        assert kinds.count("CARD_FROZEN") == 1
        assert kinds.count("CARD_UNFROZEN") == 1
        assert kinds.count("CARD_MARKED_LOST") == 1

    def test_notification_failure_does_not_break_controls(self, client, monkeypatch):
        import apps.notifications.services as nsvc
        from apps.cards.models import CardStatus
        monkeypatch.setattr(nsvc, "_create",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        u, c = _card("cn-b")
        client.force_login(u)
        resp = client.post(f"/app/cards/{c.pk}/controls/", {"action": "freeze"})
        assert resp.status_code == 302
        c.refresh_from_db()
        assert c.status == CardStatus.FROZEN

    def test_invoice_closed_and_due_notifications(self):
        from django.core.management import call_command
        from apps.cards.models import CreditStatement
        from io import StringIO

        u, c = _card("cn-c")
        # purchase inside July cycle (mocked clock) then close in August
        import datetime as dt
        from unittest.mock import patch
        from apps.cards.services import purchase
        with patch("django.utils.timezone.now",
                   return_value=dt.datetime(2026, 7, 5, 12, tzinfo=dt.timezone.utc)):
            purchase(card_id=c.pk, merchant="CN Shop", amount_raw=D("90.00"),
                     idempotency_key=f"CN-C-{c.pk}")
        call_command("close_card_invoices", reference="2026-08-01", stdout=StringIO())
        note = Notification.objects.get(recipient=u, kind="CARD_INVOICE_CLOSED")
        assert "$90.00" in note.body and "due" in note.body.lower()
        # make it overdue and rerun -> CARD_INVOICE_DUE exactly once per statement
        stmt = CreditStatement.objects.get(card=c)
        stmt.due_date = date(2026, 7, 15)
        stmt.save(update_fields=["due_date"])
        call_command("close_card_invoices", reference="2026-08-02", stdout=StringIO())
        call_command("close_card_invoices", reference="2026-08-03", stdout=StringIO())
        due = Notification.objects.filter(recipient=u, kind="CARD_INVOICE_DUE")
        assert due.count() == 1

    def test_purchase_notifications_still_single_shot(self, client):
        from apps.cards.services import CardDeclined, purchase
        u, c = _card("cn-d")
        tx = purchase(card_id=c.pk, merchant="CN X", amount_raw=D("10.00"),
                      idempotency_key=f"CN-D-{c.pk}")
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="CN Y", amount_raw=D("9999.00"),
                     idempotency_key=f"CN-D2-{c.pk}")
        kinds = list(Notification.objects.filter(recipient=u).values_list(
            "kind", flat=True))
        assert kinds.count("CARD_PURCHASE_APPROVED") == 1
        assert kinds.count("CARD_PURCHASE_DECLINED") == 1

    def test_fraud_decline_generic_no_internals(self):
        from apps.cards.services import CardDeclined, purchase
        u, c = _card("cn-e")
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="CN Z", amount_raw=D("99999.00"),
                     idempotency_key=f"CN-E-{c.pk}")
        for n in Notification.objects.filter(recipient=u, kind="CARD_PURCHASE_DECLINED"):
            blob = (n.body + str(n.metadata)).lower()
            assert "score" not in blob and "rule" not in blob and "risk_evaluation" not in blob
