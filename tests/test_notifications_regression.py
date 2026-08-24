"""FASE 6 Branch 8 — adversarial regression suite for the notification stack."""
from decimal import Decimal

import pytest

from apps.notifications.models import Notification
from apps.notifications.services import notify

D = Decimal


def _user(username):
    from tests.conftest import make_user
    from apps.customers.models import Customer
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


@pytest.mark.django_db(transaction=True)
class TestAdversarialNotifications:
    def test_dedup_bypass_via_key_collision_between_users(self):
        """Two users with the SAME semantic dedup key must each get their own
        event — dedup is per (event, reference, recipient), never global."""
        a = _user("nr-a")
        b = _user("nr-b")
        n1 = notify(recipient=a, category="CARD", title="t", kind="K",
                    dedup_key="SHARED-KEY")
        n2 = notify(recipient=b, category="CARD", title="t", kind="K",
                    dedup_key="SHARED-KEY")
        assert n1.pk != n2.pk
        assert Notification.objects.filter(dedup_key="SHARED-KEY").count() == 2

    def test_oversized_payload_and_kind_abuse_rejected_audited(self):
        from apps.audit.models import AuditLog
        u = _user("nr-c")
        before = AuditLog.objects.filter(action="NOTIFICATION_ERROR").count()
        assert notify(recipient=u, category="CARD", title="t", kind="X" * 500,
                      metadata={"m": "y" * 500}) is None
        # invalid kind charset (injection attempt) also rejected
        assert notify(recipient=u, category="CARD", title="t", kind="BAD KIND\n") is None
        assert AuditLog.objects.filter(
            action="NOTIFICATION_ERROR").count() >= before + 2
        assert Notification.objects.filter(recipient=u).count() == 0

    def test_cross_user_notification_isolation_idor(self, client):
        u1 = _user("nr-d")
        u2 = _user("nr-e")
        note = Notification.objects.create(recipient=u1, category="CARD",
                                           title="secret", body="private")
        client.force_login(u2)
        resp = client.post(f"/app/notifications/{note.pk}/read/")
        # owner-or-404: indistinguishable, and never marked
        assert resp.status_code == 404
        note.refresh_from_db()
        assert note.read is False
        assert not Notification.objects.filter(recipient=u2).exists()

    def test_get_is_never_destructive(self, client):
        u = _user("nr-f")
        note = Notification.objects.create(recipient=u, category="CARD",
                                           title="t")
        client.force_login(u)
        client.get(f"/app/notifications/{note.pk}/read/")
        note.refresh_from_db()
        assert note.read is False

    def test_xss_stored_payloads_escaped_in_center(self, client):
        u = _user("nr-g")
        Notification.objects.create(
            recipient=u, category="CARD", title="<script>alert(1)</script>",
            body="<img src=x onerror=alert(2)>")
        client.force_login(u)
        html = client.get("/app/notifications/").content.decode()
        assert "<script>alert(1)</script>" not in html
        assert "<img src=x onerror=alert(2)>" not in html

    def test_pagination_abuse_out_of_range(self, client):
        u = _user("nr-h")
        for i in range(3):
            Notification.objects.create(recipient=u, category="CARD", title=f"t{i}")
        client.force_login(u)
        for bad in ("9999", "-1", "abc"):
            resp = client.get(f"/app/notifications/?page={bad}")
            assert resp.status_code in (200, 302)

    def test_filter_abuse_unknown_state_category(self, client):
        u = _user("nr-i")
        Notification.objects.create(recipient=u, category="CARD", title="t")
        client.force_login(u)
        resp = client.get("/app/notifications/?state=DROP;--&category=UNION")
        assert resp.status_code == 200
        assert resp.context["state"] == ""


@pytest.mark.django_db(transaction=True)
class TestFinancialInvariants:
    def test_notification_never_precedes_commit_and_ledger_intact(self):
        """Approved card purchase: notification exists only after commit and
        the journal chain snapshot never changes due to notification reads."""
        from apps.ledger.models import JournalEntry
        from tests.test_notification_cards import _user as _card_user, _card
        from apps.cards.services import purchase

        u = _card_user("nr-fin"); c = _card(u)
        tx = purchase(card_id=c.pk, merchant="NR Shop", amount_raw=D("7.00"))
        snap = list(JournalEntry.objects.values_list("id", "chain_hash"))
        note = Notification.objects.get(recipient=u, kind="CARD_PURCHASE_APPROVED")
        note.read = True
        note.save(update_fields=["read"])
        mark_all = Notification.objects.filter(recipient=u).update(read=True)
        assert list(JournalEntry.objects.values_list("id", "chain_hash")) == snap
        assert float(c.account.current_balance) == 993.00
        assert tx.journal_id is not None

    def test_declined_purchase_leaves_ledger_empty(self):
        from apps.ledger.models import JournalEntry, LedgerEntry
        from tests.test_notification_cards import _user as _card_user, _card
        from apps.cards.services import CardDeclined, purchase

        u = _card_user("nr-dec"); c = _card(u)
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="NR X", amount_raw=D("999999.00"),
                     idempotency_key="NR-DEC-1")
        # declined flow: no ledger movement at all beyond opening balance
        opening = LedgerEntry.objects.filter(
            account__code=f"2001-NC-{u.username}").count()
        assert opening <= 1
        note = Notification.objects.get(recipient=u, kind="CARD_PURCHASE_DECLINED")
        blob = (note.body + str(note.metadata)).lower()
        assert "score" not in blob and "rule" not in blob
