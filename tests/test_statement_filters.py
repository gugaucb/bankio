"""FASE 5 Branch 3 — Statement filters/search tests."""
from decimal import Decimal
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.ledger.services import get_or_create_account, post_journal

D = Decimal


def _user(username):
    from tests.conftest import make_user
    from apps.customers.models import Customer
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


@pytest.fixture
def acct(db):
    from apps.accounts.models import Account
    u = _user("flt-user")
    la = get_or_create_account("2001-FLT", "A flt", is_customer=True)
    a = Account.objects.create(customer=u, account_number=f"66{u.pk:010d}", ledger_account=la)
    equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
    post_journal(reference=f"OPEN-FLT-{a.pk}", description="opening",
                 lines=[(equity, "DEBIT", D("1000.00")), (la, "CREDIT", D("1000.00"))])
    sink = get_or_create_account("6900-FLT-SINK", "Sink", type="EXPENSE")
    old = timezone.now() - timedelta(days=40)
    for i, (ref, desc) in enumerate([("FLT-OLD-A", "old grocery"), ("FLT-OLD-B", "old rent")]):
        post_journal(reference=f"{ref}-{a.pk}", description=desc,
                     lines=[(la, "DEBIT", D("10.00")), (sink, "CREDIT", D("10.00"))],
                     posted_at=old + timedelta(hours=i))
    recent = timezone.now() - timedelta(days=2)
    post_journal(reference=f"FLT-NEW-C-{a.pk}", description="recent coffee",
                 lines=[(la, "DEBIT", D("5.00")), (sink, "CREDIT", D("5.00"))],
                 posted_at=recent)
    return a, u


@pytest.mark.django_db
class TestStatementFilters:
    def test_period_7d_excludes_old(self, client, acct):
        a, u = acct
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[a.pk]) + "?period=7d")
        body = r.content.decode()
        assert "recent coffee" in body and "old grocery" not in body

    def test_period_custom_range(self, client, acct):
        a, u = acct
        start = (timezone.now() - timedelta(days=41)).date().isoformat()
        end = (timezone.now() - timedelta(days=39)).date().isoformat()
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[a.pk]) + f"?period=custom&from={start}&to={end}")
        body = r.content.decode()
        assert "old rent" in body or "old grocery" in body
        assert "recent coffee" not in body

    def test_invalid_custom_range_degrades_safely(self, client, acct):
        a, u = acct
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[a.pk]) +
                       "?period=custom&from=not-a-date&to=2026-13-99")
        assert r.status_code == 200
        assert "recent coffee" in r.content.decode()  # no filter applied, full history

    def test_inverted_range_rejected(self, client, acct):
        a, u = acct
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[a.pk]) +
                       "?period=custom&from=2026-08-20&to=2026-08-01")
        assert "recent coffee" in r.content.decode()  # ignored → unfiltered

    def test_direction_out(self, client, acct):
        a, u = acct
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[a.pk]) + "?direction=out")
        body = r.content.decode()
        assert "old grocery" in body
        # opening credit (IN) excluded
        assert body.count("−") >= 2

    def test_search_by_description(self, client, acct):
        a, u = acct
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[a.pk]) + "?q=grocery")
        body = r.content.decode()
        assert "old grocery" in body and "recent coffee" not in body

    def test_search_scoped_to_own_account(self, client, acct):
        # another account's journal with a matching description must never leak
        a, u = acct
        other_u = _user("flt-other")
        other = _mk_other(other_u)
        sink = get_or_create_account("6900-FLT-SINK", "Sink", type="EXPENSE")
        post_journal(reference=f"FLT-SECRET-{other.pk}", description="SECRETDESC transfer",
                     lines=[(other.ledger_account, "DEBIT", D("1.00")), (sink, "CREDIT", D("1.00"))])
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[a.pk]) + "?q=SECRETDESC")
        # the term is echoed back inside the search box itself — exactly once,
        # never as a result row
        assert r.content.decode().count("SECRETDESC") == 1
        assert "SECRETDESC transfer" not in r.content.decode()

    def test_source_filter_other(self, client, acct):
        a, u = acct
        client.force_login(u)
        r = client.get(reverse("app_account_statement", args=[a.pk]) + "?source=OTHER")
        assert "old grocery" in r.content.decode()  # plain journals are OTHER
        r2 = client.get(reverse("app_account_statement", args=[a.pk]) + "?source=TRANSFER")
        assert "old grocery" not in r2.content.decode()

    def test_filters_preserved_on_pagination(self, client, acct):
        a, u = acct
        la = a.ledger_account
        sink = get_or_create_account("6900-FLT-SINK", "Sink", type="EXPENSE")
        for i in range(26):
            post_journal(reference=f"FLT-PG{i}-{a.pk}", description="pg filler",
                         lines=[(la, "DEBIT", D("1.00")), (sink, "CREDIT", D("1.00"))],
                         posted_at=timezone.now() - timedelta(hours=i))
        client.force_login(u)
        url = reverse("app_account_statement", args=[a.pk])
        p1 = client.get(url + "?period=30d").content.decode()
        assert 'href="?page=2&period=30d' in p1


def _mk_other(user):
    from apps.accounts.models import Account
    la = get_or_create_account(f"2001-FLT-O-{user.username}", "other", is_customer=True)
    return Account.objects.create(customer=user, account_number=f"55{user.pk:010d}", ledger_account=la)
