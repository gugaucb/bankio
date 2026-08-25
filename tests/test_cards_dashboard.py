"""FASE 8 Branch 1 — card detail dashboard tests."""
from decimal import Decimal

import pytest

D = Decimal


def _customer(username):
    from apps.customers.models import Customer
    from tests.conftest import make_user
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _card(username, credit_limit="500.00"):
    from apps.accounts.models import Account
    from apps.cards.models import Card
    from apps.ledger.services import get_or_create_account
    u = _customer(username)
    la = get_or_create_account(f"2001-CD-{username}", f"A {username}", is_customer=True)
    a = Account.objects.create(customer=u, account_number=f"77{u.pk:010d}", ledger_account=la)
    return u, Card.objects.create(account=a, holder_name=username.upper(),
                                  type="CREDIT_CARD", credit_limit=D(credit_limit))


@pytest.mark.django_db
class TestCardDetail:
    def test_detail_shows_masked_limits_and_availability(self, client):
        from apps.cards.services import purchase
        u, c = _card("cd-a", "500.00")
        purchase(card_id=c.pk, merchant="CD Cafe", amount_raw=D("120.00"),
                 idempotency_key=f"CD-A-{c.pk}")
        client.force_login(u)
        html = client.get(f"/app/cards/{c.pk}/").content.decode()
        assert "••••" in html and c.last4 in html
        assert "$380.00" in html      # available = 500 - 120
        assert "$120.00" in html      # used
        assert "$500.00" in html      # total limit
        assert "ACTIVE" in html

    def test_idor_other_users_card_404(self, client):
        u1, c1 = _card("cd-b")
        other, _ = _card("cd-other")
        client.force_login(other)
        resp = client.get(f"/app/cards/{c1.pk}/")
        assert resp.status_code == 404

    def test_anonymous_redirected(self, client):
        _, c = _card("cd-c")
        resp = client.get(f"/app/cards/{c.pk}/")
        assert resp.status_code == 302

    def test_no_sensitive_data_in_page(self, client):
        u, c = _card("cd-d")
        client.force_login(u)
        html = client.get(f"/app/cards/{c.pk}/").content.decode()
        lower = html.lower()
        assert "cvv" not in lower
        import re
        assert not re.search(r"\bpin\b", lower)  # no PIN field/value exposed
        assert "full_pan" not in lower and "pan:" not in lower

    def test_list_links_to_detail(self, client):
        u, c = _card("cd-e")
        client.force_login(u)
        html = client.get("/app/cards/").content.decode()
        assert f"/app/cards/{c.pk}/" in html
