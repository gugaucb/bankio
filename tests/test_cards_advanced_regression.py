"""FASE 8 Branch 9 — final adversarial regression for the cards stack."""
from decimal import Decimal

import pytest

from apps.cards.services import CardDeclined, purchase
from apps.notifications.models import Notification

D = Decimal


def _customer(username):
    from apps.customers.models import Customer
    from tests.conftest import make_user
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _card(username, credit_limit="1000.00", balance="500.00"):
    from apps.accounts.models import Account
    from apps.cards.models import Card
    from apps.ledger.services import get_or_create_account, post_journal
    u = _customer(username)
    la = get_or_create_account(f"2001-RG-{username}", f"A {username}", is_customer=True)
    a = Account.objects.create(customer=u, account_number=f"90{u.pk:010d}", ledger_account=la)
    if Decimal(str(balance)) > 0:
        equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        post_journal(reference=f"OPEN-RG-{a.pk}", description="opening",
                     lines=[(equity, "DEBIT", D(balance)), (la, "CREDIT", D(balance))])
    return u, Card.objects.create(account=a, holder_name=username.upper(),
                                  type="DEBIT_CARD" if credit_limit == "0" else "CREDIT_CARD",
                                  credit_limit=D(credit_limit))


@pytest.mark.django_db(transaction=True)
class TestCardsFinancialInvariants:
    def test_approved_purchase_journal_balances_and_unique(self):
        from apps.ledger.models import JournalEntry
        u, c = _card("rg-a")
        purchase(card_id=c.pk, merchant="RG Shop", amount_raw=D("25.00"),
                 idempotency_key=f"RG-A-{c.pk}")
        purchase(card_id=c.pk, merchant="RG Shop", amount_raw=D("25.00"),
                 idempotency_key=f"RG-A-{c.pk}")  # replay
        refs = list(JournalEntry.objects.filter(description__icontains="RG Shop")
                    .values_list("reference", flat=True))
        assert len(refs) == 1  # single settlement
        j = JournalEntry.objects.get(reference=refs[0])
        total = sum((l.amount if l.side == "DEBIT" else -l.amount)
                    for l in j.entries.all())
        assert total == 0  # double-entry balanced

    def test_decline_and_risk_block_zero_ledger_movement(self):
        from apps.ledger.services import find_idempotent
        u, c = _card("rg-b")
        before = c.account.current_balance
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="RG X", amount_raw=D("9999.00"),
                     idempotency_key=f"RG-B1-{c.pk}")  # above limit -> decline
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="", amount_raw=D("-5.00"),
                     idempotency_key=f"RG-B2-{c.pk}")  # invalid amount
        assert c.account.current_balance == before
        assert find_idempotent(f"card-purchase:RG-B1-{c.pk}") is None

    def test_reversal_absence_preserves_history(self):
        """No reversal mechanism exists (FASE 8 scope): original purchases are
        immutable history — nothing rewrites or deletes them."""
        u, c = _card("rg-c")
        tx = purchase(card_id=c.pk, merchant="RG Hist", amount_raw=D("10.00"),
                      idempotency_key=f"RG-C-{c.pk}")
        tx.refresh_from_db()
        assert tx.merchant == "RG Hist" and tx.journal is not None


@pytest.mark.django_db
class TestCardsSecurityRegression:
    def test_all_card_routes_idor_safe(self, client):
        owner, c = _card("rg-owner")
        attacker, _ = _card("rg-atk")
        from apps.cards.models import CardTransaction, CreditStatement
        tx = CardTransaction.objects.create(card=c, merchant="M", amount=D("1.00"))
        stmt = CreditStatement.objects.create(
            card=c, period_start=__import__("datetime").date(2026, 6, 1),
            period_end=__import__("datetime").date(2026, 6, 30),
            amount_due=D("10.00"))
        client.force_login(attacker)
        routes_404 = [
            f"/app/cards/{c.pk}/",
            f"/app/cards/{c.pk}/transactions/",
            f"/app/cards/{c.pk}/transactions/{tx.pk}/",
            f"/app/cards/{c.pk}/invoices/",
            f"/app/cards/{c.pk}/invoices/{stmt.pk}/",
        ]
        for r in routes_404:
            assert client.get(r).status_code == 404, r
        assert client.post(f"/app/cards/{c.pk}/controls/",
                           {"action": "freeze"}).status_code == 404
        assert client.post(f"/app/cards/{c.pk}/invoices/pay/").status_code == 404

    def test_xss_in_merchant_names_escaped(self, client):
        u, c = _card("rg-xss")
        from apps.cards.models import CardTransaction
        CardTransaction.objects.create(card=c, merchant="<script>x()</script>",
                                       amount=D("1.00"))
        client.force_login(u)
        for route in (f"/app/cards/{c.pk}/transactions/", f"/app/cards/{c.pk}/"):
            html = client.get(route).content.decode()
            assert "<script>x()</script>" not in html

    def test_no_sensitive_data_anywhere(self, client):
        u, c = _card("rg-sens")
        client.force_login(u)
        html = client.get(f"/app/cards/{c.pk}/").content.decode()
        import re
        assert not re.search(r"\b(cvv|pan)\b", html.lower())
        assert str(c.account.customer.password) not in html

    def test_pagination_abuse_on_card_routes(self, client):
        u, c = _card("rg-page")
        client.force_login(u)
        assert client.get(f"/app/cards/{c.pk}/transactions/?page=9999").status_code in (200, 302)
        assert client.get(f"/app/cards/{c.pk}/invoices/?page=-3").status_code in (200, 302)

    def test_notifications_do_not_control_settlement(self, monkeypatch):
        import apps.notifications.services as nsvc
        monkeypatch.setattr(nsvc, "_create",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        u, c = _card("rg-notif")
        tx = purchase(card_id=c.pk, merchant="RG N", amount_raw=D("5.00"),
                      idempotency_key=f"RG-N-{c.pk}")
        assert tx.journal is not None  # settlement unaffected
        assert not Notification.objects.filter(recipient=u).exists()
