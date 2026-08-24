"""FASE 6 Branch 5 — card notification integration tests."""
from decimal import Decimal

import pytest

from apps.notifications.models import Notification
from apps.cards.services import purchase, CardDeclined, freeze_card, unfreeze_card, report_lost_or_stolen

D = Decimal


def _user(username):
    from tests.conftest import make_user
    from apps.customers.models import Customer
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _card(user, balance="1000.00", **kw):
    from apps.accounts.models import Account
    from apps.cards.models import Card
    from apps.ledger.services import get_or_create_account, post_journal
    la = get_or_create_account(f"2001-NC-{user.username}", f"A {user.username}", is_customer=True)
    a = Account.objects.create(customer=user, account_number=f"88{user.pk:010d}", ledger_account=la)
    if Decimal(str(balance)) > 0:
        equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        post_journal(reference=f"OPEN-NC-{a.pk}", description="opening",
                     lines=[(equity, "DEBIT", D(str(balance))), (la, "CREDIT", D(str(balance)))])
    return Card.objects.create(account=a, holder_name=user.username, **kw)


@pytest.mark.django_db(transaction=True)
class TestCardNotifications:
    def test_approved_after_posting(self):
        u = _user("ncx-a"); c = _card(u)
        tx = purchase(card_id=c.pk, merchant="NC Cafe", amount_raw=D("12.00"))
        note = Notification.objects.get(recipient=u, kind="CARD_PURCHASE_APPROVED")
        assert "approved" in note.body and tx.journal.reference in note.body

    def test_declined_limit_customer_safe(self):
        u = _user("ncx-b"); c = _card(u, tx_limit=D("10.00"))
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="NC Shop", amount_raw=D("50.00"))
        note = Notification.objects.get(recipient=u, kind="CARD_PURCHASE_DECLINED")
        assert "limit" in note.body.lower()
        # no fraud internals in the payload
        assert "score" not in str(note.metadata).lower()

    def test_declined_frozen(self):
        u = _user("ncx-c"); c = _card(u)
        freeze_card(u, c.pk)
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="NC X", amount_raw=D("5.00"))
        note = Notification.objects.get(recipient=u, kind="CARD_PURCHASE_DECLINED")
        assert "frozen" in note.body.lower()

    def test_declined_online_disabled(self):
        u = _user("ncx-d"); c = _card(u, online_enabled=False)
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="NC Web", amount_raw=D("5.00"), online=True)
        assert Notification.objects.filter(
            recipient=u, kind="CARD_PURCHASE_DECLINED",
            metadata__reason="ONLINE_DISABLED").exists()

    def test_declined_international_disabled(self):
        u = _user("ncx-e"); c = _card(u, international_enabled=False)
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="NC Intl", amount_raw=D("5.00"),
                     international=True)
        assert Notification.objects.filter(
            recipient=u, kind="CARD_PURCHASE_DECLINED",
            metadata__reason="INTERNATIONAL_DISABLED").exists()

    def test_dedup_replay_single_notification(self):
        from apps.ledger.services import find_idempotent
        u = _user("ncx-f"); c = _card(u)
        key = f"NC-RP-{u.pk}"
        purchase(card_id=c.pk, merchant="NC Rep", amount_raw=D("3.00"),
                 idempotency_key=key)
        before = Notification.objects.count()
        # idempotent replay returns the settled tx; on_commit hook dedups by journal ref
        purchase(card_id=c.pk, merchant="NC Rep", amount_raw=D("3.00"),
                 idempotency_key=key)
        assert Notification.objects.count() == before
        assert Notification.objects.filter(kind="CARD_PURCHASE_APPROVED").count() == 1

    def test_notification_failure_does_not_break_purchase(self, monkeypatch):
        import apps.notifications.services as nsvc
        u = _user("ncx-g"); c = _card(u)
        monkeypatch.setattr(nsvc, "_create",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        tx = purchase(card_id=c.pk, merchant="NC Boom", amount_raw=D("2.00"))
        assert tx.journal_id is not None
        bal = float(c.account.current_balance)
        assert bal == 998.00  # settlement unaffected
        from apps.audit.models import AuditLog
        assert AuditLog.objects.filter(action="NOTIFICATION_ERROR").exists()

    def test_ledger_snapshot_unchanged(self):
        from apps.ledger.models import JournalEntry
        u = _user("ncx-h"); c = _card(u)
        purchase(card_id=c.pk, merchant="NC Snap", amount_raw=D("4.00"))
        snap = list(JournalEntry.objects.values_list("id", "chain_hash"))
        n = Notification.objects.get(recipient=u, kind="CARD_PURCHASE_APPROVED")
        n.read = True; n.save(update_fields=["read"])
        assert list(JournalEntry.objects.values_list("id", "chain_hash")) == snap

    def test_risk_decline_generic_message(self, settings):
        settings.FRAUD_MODE = "ENFORCEMENT"
        from apps.fraud.modes import set_mode
        from tests.conftest import make_user
        mgr = make_user(f"ncx-mgr", role="FRAUD_MANAGER")
        set_mode("ENFORCEMENT", actor=mgr)
        u = _user("ncx-i"); c = _card(u)
        with pytest.raises(CardDeclined) as ei:
            purchase(card_id=c.pk, merchant="NC Risk", amount_raw=D("999999.00"),
                     idempotency_key=f"NC-RISK-{u.pk}")
        # whatever the decline reason, customer text never exposes rule names/scores
        notes = Notification.objects.filter(recipient=u, kind="CARD_PURCHASE_DECLINED")
        for n in notes:
            blob = (n.body + str(n.metadata)).lower()
            assert "score" not in blob and "rule" not in blob and "risk_evaluation" not in blob
