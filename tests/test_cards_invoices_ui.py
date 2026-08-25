"""FASE 8 Branch 6 — invoice customer-facing UI tests."""
from datetime import date
from decimal import Decimal

import pytest

from apps.cards.models import CreditStatement

D = Decimal


def _customer(username):
    from apps.customers.models import Customer
    from tests.conftest import make_user
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _credit_card(username):
    from apps.accounts.models import Account
    from apps.cards.models import Card
    from apps.ledger.services import get_or_create_account
    u = _customer(username)
    la = get_or_create_account(f"2001-IV-{username}", f"A {username}", is_customer=True)
    a = Account.objects.create(customer=u, account_number=f"22{u.pk:010d}", ledger_account=la)
    return u, Card.objects.create(account=a, holder_name=username.upper(),
                                  type="CREDIT_CARD", credit_limit=D("500.00"))


@pytest.mark.django_db(transaction=True)
class TestInvoiceUI:
    def _buy(self, card, merchant, amount, y, m, d):
        import datetime as dt
        from unittest.mock import patch
        from apps.cards.services import purchase
        with patch("django.utils.timezone.now",
                   return_value=dt.datetime(y, m, d, 12, tzinfo=dt.timezone.utc)):
            purchase(card_id=card.pk, merchant=merchant, amount_raw=D(amount),
                     idempotency_key=f"IV-{card.pk}-{merchant}")

    def test_current_invoice_shows_open_cycle(self, client):
        u, c = _credit_card("iv-a")
        self._buy(c, "Open Shop", "45.00", 2026, 8, 5)
        client.force_login(u)
        html = client.get(f"/app/cards/{c.pk}/invoices/").content.decode()
        assert "$45.00" in html and "OPEN" in html and "Open Shop" in html

    def test_previous_statements_listed_with_status(self, client):
        u, c = _credit_card("iv-b")
        CreditStatement.objects.create(card=c, period_start=date(2026, 7, 1),
                                       period_end=date(2026, 7, 31),
                                       amount_due=D("80.00"),
                                       due_date=date(2026, 8, 10))
        CreditStatement.objects.create(card=c, period_start=date(2026, 6, 1),
                                       period_end=date(2026, 6, 30),
                                       amount_due=D("20.00"), paid=True,
                                       paid_at=__import__("django.utils.timezone", fromlist=["now"]).now())
        client.force_login(u)
        html = client.get(f"/app/cards/{c.pk}/invoices/").content.decode()
        assert "July" in html or "Julho" in html or "2026" in html
        assert "PAID" in html and "UNPAID" in html

    def test_invoice_detail_idor(self, client):
        u1, c1 = _credit_card("iv-owner")
        s1 = CreditStatement.objects.create(card=c1, period_start=date(2026, 6, 1),
                                            period_end=date(2026, 6, 30),
                                            amount_due=D("50.00"))
        other_u, c2 = _credit_card("iv-other")
        client.force_login(other_u)
        assert client.get(f"/app/cards/{c1.pk}/invoices/{s1.pk}/").status_code == 404
        # foreign statement under own card id also 404
        client.force_login(u1)
        s2 = CreditStatement.objects.create(card=c2, period_start=date(2026, 6, 1),
                                            period_end=date(2026, 6, 30),
                                            amount_due=D("60.00"))
        assert client.get(f"/app/cards/{c1.pk}/invoices/{s2.pk}/").status_code == 404
        resp = client.get(f"/app/cards/{c1.pk}/invoices/{s1.pk}/")
        assert resp.status_code == 200

    def test_closed_invoice_composition_immutable_after_new_purchases(self, client):
        u, c = _credit_card("iv-c")
        self._buy(c, "Cycle Shop", "30.00", 2026, 5, 10)
        from apps.cards.billing import close_card_statements
        stmt = close_card_statements(reference=date(2026, 6, 1))[0]
        # new purchase AFTER closing must not change the closed invoice
        self._buy(c, "Later Shop", "70.00", 2026, 6, 3)
        client.force_login(u)
        html = client.get(f"/app/cards/{c.pk}/invoices/{stmt.pk}/").content.decode()
        assert "Cycle Shop" in html and "Later Shop" not in html
        assert "$30.00" in html

    def test_pagination_of_previous_invoices(self, client):
        u, c = _credit_card("iv-d")
        for i in range(14):
            month = i % 12 + 1
            year = 2024 if i >= 12 else 2025
            CreditStatement.objects.create(
                card=c, period_start=date(year, month, 1),
                period_end=date(year, month, 28), amount_due=D("1.00"))
        client.force_login(u)
        resp = client.get(f"/app/cards/{c.pk}/invoices/")
        assert resp.context["stmts_page"].paginator.num_pages == 2
