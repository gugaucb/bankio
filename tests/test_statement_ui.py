"""FASE 5 Branch 2 — Statement UI tests."""
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.ledger.services import get_or_create_account, post_journal

D = Decimal


def _user(username, number):
    from tests.conftest import make_user
    from apps.customers.models import Customer
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _account(user, balance="1000.00"):
    from apps.accounts.models import Account
    la = get_or_create_account(f"2001-STMTUI-{user.username}", f"A {user.username}", is_customer=True)
    acct = Account.objects.create(customer=user, account_number=f"77{user.pk:010d}", ledger_account=la)
    if Decimal(str(balance)) > 0:
        equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        post_journal(reference=f"OPEN-UI-{acct.pk}", description="opening",
                     lines=[(equity, "DEBIT", D(str(balance))), (la, "CREDIT", D(str(balance)))])
    return acct


@pytest.mark.django_db
class TestStatementUI:
    def test_owner_sees_statement(self, client):
        u = _user("stmtui-a", 1); acct = _account(u)
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[acct.pk]))
        assert r.status_code == 200
        body = r.content.decode()
        assert "•••• " in body and acct.account_number[-4:] in body
        assert "$1,000.00" in body or "1000.00" in body
        assert "opening" in body.lower()

    def test_empty_state(self, client):
        u = _user("stmtui-b", 1); acct = _account(u, "0.00")
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[acct.pk]))
        assert b"No movements" in r.content

    def test_idor_foreign_account_404(self, client):
        a = _user("stmtui-c", 1); acct_a = _account(a)
        b = _user("stmtui-d", 1)
        client.force_login(b)
        r = client.get(reverse("app_account_statement", args=[acct_a.pk]))
        assert r.status_code == 404
        # no content leak confirming the account exists
        assert b"1,000.00" not in r.content and b"1000.00" not in r.content

    def test_anonymous_redirects_to_login(self, client):
        a = _user("stmtui-e", 1); acct = _account(a)
        r = client.get(reverse("app_account_statement", args=[acct.pk]))
        assert r.status_code == 302 and "/login/" in r.url

    def test_staff_redirected_by_customer_only(self, client):
        staff = _user("stmtui-f", 1)
        staff.is_staff = True; staff.role = "ADMIN"; staff.save()
        client.force_login(staff)
        r = client.get(reverse("app_account_statement", args=[1]))
        assert r.status_code == 302

    def test_pagination_server_side(self, client):
        u = _user("stmtui-g", 1); acct = _account(u)
        la = acct.ledger_account
        sink = get_or_create_account(f"6900-SINKUI-{acct.pk}", "Sink", type="EXPENSE")
        for i in range(30):
            post_journal(reference=f"PUI{i}-{acct.pk}", description="m",
                         lines=[(la, "DEBIT", D("1.00")), (sink, "CREDIT", D("1.00"))])
        client.force_login(u)
        url = reverse("app_account_statement", args=[acct.pk])
        p1 = client.get(url).content.decode()
        assert "Page 1 of 2" in p1
        p2 = client.get(url + "?page=2").content.decode()
        assert "Page 2 of 2" in p2

    def test_invalid_page_falls_back(self, client):
        u = _user("stmtui-h", 1); acct = _account(u, "0.00")
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[acct.pk]) + "?page=99")
        assert r.status_code == 404 or b"No movements" in r.content
