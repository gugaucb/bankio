"""FASE 5 Branch 6 — adversarial + reconciliation + immutability tests."""
from decimal import Decimal
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.ledger.models import JournalEntry
from apps.ledger.services import get_or_create_account, post_journal, reverse_journal

D = Decimal


def _user(username):
    from tests.conftest import make_user
    from apps.customers.models import Customer
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _account(user, balance="1000.00"):
    from apps.accounts.models import Account
    la = get_or_create_account(f"2001-AD-{user.username}", f"A {user.username}", is_customer=True)
    a = Account.objects.create(customer=user, account_number=f"22{user.pk:010d}", ledger_account=la)
    if Decimal(str(balance)) > 0:
        equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        post_journal(reference=f"OPEN-AD-{a.pk}", description="opening",
                     lines=[(equity, "DEBIT", D(str(balance))), (la, "CREDIT", D(str(balance)))])
    return a


@pytest.mark.django_db
class TestStatementAdversarial:
    def test_page_manipulation_safe(self, client):
        u = _user("ad-a"); a = _account(u, "0.00")
        client.force_login(u)
        url = reverse("app_account_statement", args=[a.pk])
        for qs in ("?page=-5", "?page=99999999999999999999", "?page=abc"):
            r = client.get(url + qs)
            assert r.status_code in (200, 404)

    def test_xss_description_escaped_everywhere(self, client):
        u = _user("ad-b"); a = _account(u, "0.00")
        evil_src = get_or_create_account("1000-AD-EVIL", "Evil", type="ASSET")
        post_journal(reference="AD-XSS-1",
                     description="<script>alert(1)</script>",
                     lines=[(evil_src, "DEBIT", D("1.00")), (a.ledger_account, "CREDIT", D("1.00"))])
        client.force_login(u)
        for name in ("app_account_statement", "app_account_statement_print",
                     "app_transaction_detail"):
            body = client.get(reverse(name, args=[a.pk] if "statement" in name and "transaction" not in name else ["AD-XSS-1"])).content.decode()
            assert "<script>alert(1)</script>" not in body, name

    def test_draft_not_leaked_via_detail_or_receipt(self, client):
        u = _user("ad-c"); a = _account(u, "0.00")
        j = JournalEntry.objects.create(reference="AD-DRAFT-1", description="draft", status="DRAFT")
        from apps.ledger.models import LedgerEntry
        LedgerEntry.objects.create(journal=j, account=a.ledger_account, side="CREDIT", amount=D("9.00"))
        client.force_login(u)
        assert client.get(reverse("app_transaction_detail", args=["AD-DRAFT-1"])).status_code == 404
        assert client.get(reverse("app_transaction_receipt", args=["AD-DRAFT-1"])).status_code == 404

    def test_post_to_readonly_views_does_not_mutate(self, client):
        from django.test import Client as C
        s = _user("ad-d"); sa = _account(s)
        r = _user("ad-e"); ra = _account(r, "10.00")
        from apps.transfers.services import execute_transfer
        t, _ = execute_transfer(actor=s, source_account_id=sa.pk, amount=D("2"),
                                destination_account_id=ra.pk, idempotency_key="AD-POST-1")
        ref = t.journal.reference
        chain_before = JournalEntry.objects.get(reference=ref).chain_hash
        bal_before = float(sa.current_balance)
        c = C(); c.force_login(s)
        for url in (reverse("app_transaction_detail", args=[ref]),
                    reverse("app_transaction_receipt", args=[ref]),
                    reverse("app_account_statement_export", args=[sa.pk])):
            c.post(url, {"amount": "999999"})
        assert JournalEntry.objects.get(reference=ref).chain_hash == chain_before
        assert float(sa.current_balance) == bal_before

    def test_double_submit_idempotency_statement_stable(self, client):
        s = _user("ad-f"); sa = _account(s)
        r = _user("ad-g"); ra = _account(r, "10.00")
        from apps.transfers.services import execute_transfer
        execute_transfer(actor=s, source_account_id=sa.pk, amount=D("2"),
                         destination_account_id=ra.pk, idempotency_key="AD-REPLAY-9")
        # second identical submit must be a no-op replay
        t2, created = execute_transfer(actor=s, source_account_id=sa.pk, amount=D("2"),
                                       destination_account_id=ra.pk, idempotency_key="AD-REPLAY-9")
        assert not created
        client.force_login(s)
        text = b"".join(client.get(reverse("app_account_statement_export", args=[sa.pk]))
                        .streaming_content).decode()
        out_rows = [l for l in text.strip().splitlines()[1:] if ",2.00," in l]
        assert len(out_rows) == 1

    def test_cross_currency_never_summed(self, client):
        u = _user("ad-h"); a = _account(u)
        eur_la = get_or_create_account(f"2001-AD-EUR-{u.pk}", "EUR", is_customer=True)
        src = get_or_create_account(f"1000-AD-EURSRC-{u.pk}", "S", type="ASSET")
        post_journal(reference="AD-EUR-1", description="euro op",
                     lines=[(src, "DEBIT", D("7.00")), (eur_la, "CREDIT", D("7.00"))])
        client.force_login(u)
        text = b"".join(client.get(reverse("app_account_statement_export", args=[a.pk]))
                        .streaming_content).decode()
        assert "euro op" not in text


@pytest.mark.django_db
class TestReconciliationAndImmutability:
    def test_last_balance_after_equals_service_mixed_ops(self):
        s = _user("rc-a"); sa = _account(s, "2000.00")
        r = _user("rc-b"); ra = _account(r, "100.00")
        from apps.transfers.services import execute_transfer, reverse_transfer
        from apps.accounts.statement import statement_lines, statement_queryset, closing_balance_matches
        execute_transfer(actor=s, source_account_id=sa.pk, amount=D("150"),
                         destination_account_id=ra.pk, idempotency_key="RC-1")
        t2, _ = execute_transfer(actor=r, source_account_id=ra.pk, amount=D("30"),
                                 destination_account_id=sa.pk, idempotency_key="RC-2")
        t3, _ = execute_transfer(actor=s, source_account_id=sa.pk, amount=D("20"),
                                 destination_account_id=ra.pk, idempotency_key="RC-3")
        reverse_transfer(t3, actor=s)
        lines = statement_lines(sa, statement_queryset(sa))
        assert closing_balance_matches(sa, lines[-1].balance_after)
        assert lines[-1].balance_after == sa.current_balance

    def test_full_immutability_sweep(self, client):
        from django.test import Client as C
        from apps.audit.models import AuditLog
        from apps.fraud.models import RiskEvaluation
        u = _user("rc-c"); a = _account(u)
        snapshot = {
            "journals": list(JournalEntry.objects.values_list("id", "chain_hash")),
            "balance": str(a.current_balance),
            "risk": list(RiskEvaluation.objects.values_list("id", "decision")),
            "audit_count": AuditLog.objects.count(),
        }
        c = C(); c.force_login(u)
        c.get(reverse("app_account_statement", args=[a.pk]))
        c.get(reverse("app_account_statement_print", args=[a.pk]))
        c.get(reverse("app_account_statement_export", args=[a.pk]))
        c.get(reverse("app_transactions"))
        assert list(JournalEntry.objects.values_list("id", "chain_hash")) == snapshot["journals"]
        assert str(a.current_balance) == snapshot["balance"]
        assert list(RiskEvaluation.objects.values_list("id", "decision")) == snapshot["risk"]
