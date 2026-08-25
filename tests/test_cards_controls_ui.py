"""FASE 8 Branch 2 — customer card controls & lifecycle tests."""
from decimal import Decimal

import pytest

from apps.cards.models import CardStatus

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
    la = get_or_create_account(f"2001-CL-{username}", f"A {username}", is_customer=True)
    a = Account.objects.create(customer=u, account_number=f"66{u.pk:010d}", ledger_account=la)
    return u, Card.objects.create(account=a, holder_name=username.upper(), **kw)


@pytest.mark.django_db
class TestCardControlsUI:
    def test_freeze_unfreeze_via_post(self, client):
        from apps.cards.models import CardStatus
        u, c = _card("cl-a")
        client.force_login(u)
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "freeze"})
        c.refresh_from_db()
        assert c.status == CardStatus.FROZEN
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "unfreeze"})
        c.refresh_from_db()
        assert c.status == CardStatus.ACTIVE

    def test_online_international_toggles(self, client):
        u, c = _card("cl-b")
        client.force_login(u)
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "toggle_online"})
        c.refresh_from_db()
        assert c.online_enabled is False
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "toggle_international"})
        c.refresh_from_db()
        assert c.international_enabled is True

    def test_report_lost_is_terminal(self, client):
        from apps.cards.models import CardStatus
        from apps.cards.services import CardDeclined, unfreeze_card
        u, c = _card("cl-c")
        client.force_login(u)
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "report_lost"})
        c.refresh_from_db()
        assert c.status == CardStatus.BLOCKED
        # lost is terminal: unfreeze via service must refuse
        with pytest.raises(CardDeclined):
            unfreeze_card(u, c.pk)

    def test_get_is_never_destructive(self, client):
        from apps.cards.models import CardStatus
        u, c = _card("cl-d")
        client.force_login(u)
        client.get(f"/app/cards/{c.pk}/controls/")
        c.refresh_from_db()
        assert c.status == CardStatus.ACTIVE and c.online_enabled is True

    def test_idor_cannot_control_other_users_card(self, client):
        from apps.audit.models import AuditLog
        u1, c1 = _card("cl-owner")
        attacker, _ = _card("cl-attacker")
        client.force_login(attacker)
        resp = client.post(f"/app/cards/{c1.pk}/controls/", {"action": "freeze"})
        assert resp.status_code == 404
        c1.refresh_from_db()
        assert c1.status != CardStatus.FROZEN

    def test_unknown_action_safe_and_csrf_enforced(self, client, settings):
        u, c = _card("cl-e")
        client.force_login(u)
        settings.DEBUG = False
        resp = client.post(f"/app/cards/{c.pk}/controls/", {"action": "self_destruct"})
        assert resp.status_code == 302  # safe redirect, nothing changed
        # CSRF middleware present
        from django.conf import settings as dj_settings
        assert "django.middleware.csrf.CsrfViewMiddleware" in dj_settings.MIDDLEWARE

    def test_double_submit_freeze_idempotent(self, client):
        from apps.cards.models import CardStatus
        u, c = _card("cl-f")
        client.force_login(u)
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "freeze"})
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "freeze"})
        c.refresh_from_db()
        assert c.status == CardStatus.FROZEN

    def test_frozen_card_cannot_purchase_but_active_can(self, client):
        from apps.accounts.models import Account
        from apps.cards.services import CardDeclined, purchase
        u, c = _card("cl-g")
        la = c.account.ledger_account
        from apps.ledger.services import get_or_create_account, post_journal
        equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        post_journal(reference=f"OPEN-CL-{c.pk}", description="opening",
                     lines=[(equity, "DEBIT", D("100.00")), (la, "CREDIT", D("100.00"))])
        client.force_login(u)
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "freeze"})
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="CL X", amount_raw=D("5.00"),
                     idempotency_key=f"CL-G1-{c.pk}")
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "unfreeze"})
        tx = purchase(card_id=c.pk, merchant="CL Y", amount_raw=D("5.00"),
                      idempotency_key=f"CL-G2-{c.pk}")
        assert not tx.declined

    def test_replacement_request_audited_after_lost(self, client):
        from apps.audit.models import AuditLog
        from apps.customers.models import Customer
        u, c = _card("cl-h")
        acct = c.account
        client.force_login(u)
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "report_lost"})
        client.post("/app/cards/", {
            "request_card": "1", "account": acct.pk,
            "type": "CREDIT_CARD", "limit": "2000"})
        actions = list(AuditLog.objects.filter(actor=u).values_list("action", flat=True))
        assert "CARD_REPORTED_LOST" in actions
        assert "CARD_REPLACEMENT_REQUESTED" in actions

    def test_control_actions_audited(self, client):
        from apps.audit.models import AuditLog
        u, c = _card("cl-i")
        client.force_login(u)
        client.post(f"/app/cards/{c.pk}/controls/", {"action": "freeze"})
        assert AuditLog.objects.filter(actor=u, action="CARD_UPDATED").exists()
