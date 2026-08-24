"""FASE 5 Branch 5 — CSV export + print statement tests."""
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


def _account(user, balance="1000.00"):
    from apps.accounts.models import Account
    la = get_or_create_account(f"2001-EX-{user.username}", f"A {user.username}", is_customer=True)
    a = Account.objects.create(customer=user, account_number=f"33{user.pk:010d}", ledger_account=la)
    if Decimal(str(balance)) > 0:
        equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        post_journal(reference=f"OPEN-EX-{a.pk}", description="opening",
                     lines=[(equity, "DEBIT", D(str(balance))), (la, "CREDIT", D(str(balance)))])
    return a


@pytest.mark.django_db
class TestStatementExport:
    def test_csv_content_and_direction_columns(self, client):
        u = _user("ex-a"); a = _account(u)
        sink = get_or_create_account("6900-EX-SINK", "Sink", type="EXPENSE")
        post_journal(reference="EX-OUT-1", description="coffee",
                     lines=[(a.ledger_account, "DEBIT", D("3.50")), (sink, "CREDIT", D("3.50"))])
        client.force_login(u)
        r = client.get(reverse("app_account_statement_export", args=[a.pk]))
        assert r.status_code == 200 and r["Content-Type"] == "text/csv"
        text = b"".join(r.streaming_content).decode()
        lines_ = [l.split(",") for l in text.strip().splitlines()]
        assert lines_[0] == ["Date", "Description", "Type", "In", "Out", "Balance", "Reference"]
        opening = lines_[1]
        assert opening[3] == "1000.00" and opening[4] == ""  # In column
        out_row = lines_[2]
        assert "coffee" in out_row and out_row[4] == "3.50" and out_row[3] == ""

    def test_csv_respects_filters(self, client):
        u = _user("ex-b"); a = _account(u)
        client.force_login(u)
        r = client.get(reverse("app_account_statement_export", args=[a.pk]) + "?q=nomatch")
        text = b"".join(r.streaming_content).decode().strip().splitlines()
        assert len(text) == 1  # header only

    def test_idor_export_blocked(self, client):
        owner = _user("ex-c"); a = _account(owner)
        intruder = _user("ex-d")
        client.force_login(intruder)
        r = client.get(reverse("app_account_statement_export", args=[a.pk]))
        assert r.status_code == 404

    def test_csv_injection_sanitized(self, client):
        u = _user("ex-e"); a = _account(u, "0.00")
        evil = get_or_create_account("1000-EVIL-SRC", "Evil", type="ASSET")
        post_journal(reference="EX-EVIL-1",
                     description="=HYPERLINK(\"http://evil\",\"x\")",
                     lines=[(evil, "DEBIT", D("1.00")), (a.ledger_account, "CREDIT", D("1.00"))])
        client.force_login(u)
        r = client.get(reverse("app_account_statement_export", args=[a.pk]))
        import csv as _csv
        import io
        rows = list(_csv.reader(io.StringIO(b"".join(r.streaming_content).decode())))
        desc = [r[1] for r in rows if "HYPERLINK" in "".join(r)][0]
        assert desc.startswith("'="), desc  # neutralized, cannot execute

    def test_export_read_only(self, client):
        from apps.audit.models import AuditLog
        from apps.ledger.models import JournalEntry
        u = _user("ex-f"); a = _account(u)
        chain_before = list(JournalEntry.objects.values_list("chain_hash", flat=True))
        balance_before = float(a.current_balance)
        client.force_login(u)
        client.get(reverse("app_account_statement_export", args=[a.pk]))
        assert list(JournalEntry.objects.values_list("chain_hash", flat=True)) == chain_before
        assert float(a.current_balance) == balance_before
        # export audited without financial content
        log = AuditLog.objects.filter(action="STATEMENT_EXPORTED").order_by("-id").first()
        assert log is not None and "1000.00" not in str(log.metadata)

    def test_print_view(self, client):
        u = _user("ex-g"); a = _account(u)
        client.force_login(u)
        r = client.get(reverse("app_account_statement_print", args=[a.pk]))
        body = r.content.decode()
        assert r.status_code == 200 and "Account Statement" in body and "1000.00" in body

    def test_large_volume_no_nplus1(self, client):
        u = _user("ex-h"); a = _account(u, "0.00")
        la = a.ledger_account
        sink = get_or_create_account("6900-EX-SINK", "Sink", type="EXPENSE")
        base = timezone.now() - timedelta(days=1)
        for i in range(60):
            post_journal(reference=f"EX-VOL{i}", description="vol",
                         lines=[(la, "DEBIT", D("1.00")), (sink, "CREDIT", D("1.00"))],
                         posted_at=base + timedelta(seconds=i))
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        client.force_login(u)
        with CaptureQueriesContext(connection) as ctx:
            r = client.get(reverse("app_account_statement_export", args=[a.pk]))
            body = b"".join(r.streaming_content).decode()
        assert body.count("\n") >= 61
        # chunked projection: constant query count regardless of 60 rows
        assert len(ctx.captured_queries) < 25, len(ctx.captured_queries)
