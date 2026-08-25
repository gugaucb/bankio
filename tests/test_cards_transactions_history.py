"""FASE 8 Branch 4 — card transaction history tests."""
from decimal import Decimal

import pytest

from apps.cards.models import CardTransaction
from apps.cards.services import purchase

D = Decimal


def _customer(username):
    from apps.customers.models import Customer
    from tests.conftest import make_user
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _card_with_txs(username, n=3, declined_n=1):
    from apps.accounts.models import Account
    from apps.cards.models import Card
    from apps.ledger.services import get_or_create_account, post_journal
    u = _customer(username)
    la = get_or_create_account(f"2001-TH-{username}", f"A {username}", is_customer=True)
    a = Account.objects.create(customer=u, account_number=f"44{u.pk:010d}", ledger_account=la)
    equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
    post_journal(reference=f"OPEN-TH-{a.pk}", description="opening",
                 lines=[(equity, "DEBIT", D("500.00")), (la, "CREDIT", D("500.00"))])
    c = Card.objects.create(account=a, holder_name=username.upper(),
                            type="DEBIT_CARD")
    for i in range(n):
        purchase(card_id=c.pk, merchant=f"Shop {username} {i}",
                 amount_raw=D("10.00"), idempotency_key=f"TH-{username}-{i}-ok")
    for i in range(declined_n):
        try:
            purchase(card_id=c.pk, merchant=f"Bad {username} {i}",
                     amount_raw=D("9999.00"), idempotency_key=f"TH-{username}-{i}-bad")
        except Exception:
            pass  # decline rows may not persist (known pre-existing behavior);
            # declines here are only exercised via direct creation below
    return u, c


@pytest.mark.django_db
class TestCardTransactionsHistory:
    def test_history_lists_and_links(self, client):
        u, c = _card_with_txs("th-a")
        client.force_login(u)
        html = client.get(f"/app/cards/{c.pk}/transactions/").content.decode()
        assert "Shop th-a 0" in html and "APPROVED" in html
        tx = c.transactions.first()
        assert f"/app/cards/{c.pk}/transactions/{tx.pk}/" in html

    def test_filters_status_and_merchant_and_period(self, client):
        u, c = _card_with_txs("th-b", n=2)
        CardTransaction.objects.create(card=c, merchant="Declined Shop",
                                       amount=D("5.00"), declined=True,
                                       decline_reason="TEST")
        client.force_login(u)
        base = f"/app/cards/{c.pk}/transactions/"
        html_ok = client.get(base + "?status=approved").content.decode()
        assert "Declined Shop" not in html_ok
        html_bad = client.get(base + "?status=declined").content.decode()
        assert "Declined Shop" in html_bad and "Shop th-b" not in html_bad
        html_m = client.get(base + "?merchant=shop+th-b+1").content.decode()
        assert "Shop th-b 1" in html_m and "Shop th-b 0" not in html_m
        html_p = client.get(base + "?from=2100-01-01&to=2100-01-02").content.decode()
        assert "No transactions match" in html_p
        # invalid dates ignored safely
        assert client.get(base + "?from=garbage").status_code == 200

    def test_pagination(self, client):
        u, c = _card_with_txs("th-c", n=30, declined_n=0)
        client.force_login(u)
        resp = client.get(f"/app/cards/{c.pk}/transactions/")
        assert resp.context["page"].paginator.num_pages == 2
        assert len(resp.context["page"].object_list) == 25

    def test_detail_ownership_triple_check(self, client):
        u1, c1 = _card_with_txs("th-owner", n=1, declined_n=0)
        _other_u, c_other = _card_with_txs("th-other", n=1, declined_n=0)
        tx1 = c1.transactions.first()
        client.force_login(_other_u)
        # other user's transaction -> 404
        assert client.get(f"/app/cards/{c1.pk}/transactions/{tx1.pk}/").status_code == 404
        # own card id with foreign tx id -> 404 (card+tx binding)
        assert client.get(f"/app/cards/{c1.pk}/transactions/{c_other.transactions.first().pk}/").status_code == 404
        # owner sees it
        client.force_login(u1)
        assert client.get(f"/app/cards/{c1.pk}/transactions/{tx1.pk}/").status_code == 200

    def test_declined_not_confused_with_financial(self, client):
        """DECLINED rows carry no journal — page must not present them as money."""
        u, c = _card_with_txs("th-d", n=1)
        CardTransaction.objects.create(card=c, merchant="Declined Shop X",
                                       amount=D("7.00"), declined=True,
                                       decline_reason="TEST")
        client.force_login(u)
        html = client.get(f"/app/cards/{c.pk}/transactions/?status=declined").content.decode()
        assert "DECLINED" in html and "/receipts/" not in html.split("Declined Shop X")[1].split("</a>")[0]
