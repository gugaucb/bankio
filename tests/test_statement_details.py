"""FASE 5 Branch 4 — Transaction detail + receipts tests."""
from decimal import Decimal

import pytest
from django.urls import reverse

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
    la = get_or_create_account(f"2001-DT-{user.username}", f"A {user.username}", is_customer=True)
    a = Account.objects.create(customer=user, account_number=f"44{user.pk:010d}", ledger_account=la)
    if Decimal(str(balance)) > 0:
        equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        post_journal(reference=f"OPEN-DT-{a.pk}", description="opening",
                     lines=[(equity, "DEBIT", D(str(balance))), (la, "CREDIT", D(str(balance)))])
    return a


@pytest.mark.django_db
class TestTransactionDetails:
    def _transfer(self, sender_acct, receiver_acct, amount="25.00"):
        from apps.transfers.services import execute_transfer
        t, created = execute_transfer(
            actor=sender_acct.customer, source_account_id=sender_acct.pk,
            amount=D(amount), destination_account_id=receiver_acct.pk,
            idempotency_key=f"DT-{sender_acct.pk}-{receiver_acct.pk}",
            description="dt test")
        assert created or t.status == "COMPLETED"
        return t

    def test_detail_visible_to_owner(self, client):
        s = _user("dt-a"); sa = _account(s)
        r = _user("dt-b"); ra = _account(r, "50.00")
        t = self._transfer(sa, ra)
        client.force_login(s)
        resp = client.get(reverse("app_transaction_detail", args=[t.journal.reference]))
        body = resp.content.decode()
        assert resp.status_code == 200 and "COMPLETED" in body and t.reference in body
        # sensitive internals never exposed
        for secret in ("idempotency", "chain_hash", "payload_hash", str(t.idempotency_key)):
            assert secret not in body

    def test_receipt_for_completed(self, client):
        s = _user("dt-c"); sa = _account(s)
        r = _user("dt-d"); ra = _account(r, "50.00")
        t = self._transfer(sa, ra)
        client.force_login(s)
        resp = client.get(reverse("app_transaction_receipt", args=[t.journal.reference]))
        body = resp.content.decode()
        assert resp.status_code == 200 and t.reference in body
        assert f"•••• {ra.account_number[-4:]}" in body  # masked counterparty
        assert ra.account_number not in body             # never full number

    def test_no_receipt_for_plain_journal(self, client):
        u = _user("dt-e"); a = _account(u)
        post_journal(reference="DT-PLAIN-1", description="plain",
                     lines=[(a.ledger_account, "CREDIT", D("5.00")),
                            (get_or_create_account("1000-DT-SRC", "S", type="ASSET"),
                             "DEBIT", D("5.00"))])
        client.force_login(u)
        assert client.get(reverse("app_transaction_receipt", args=["DT-PLAIN-1"])).status_code == 404
        # detail still accessible (journal-level view)
        assert client.get(reverse("app_transaction_detail", args=["DT-PLAIN-1"])).status_code == 200

    def test_reversal_links_both_ways(self, client):
        from apps.transfers.services import reverse_transfer
        s = _user("dt-f"); sa = _account(s)
        r = _user("dt-g"); ra = _account(r, "50.00")
        t = self._transfer(sa, ra)
        reverse_transfer(t, actor=s)
        client.force_login(s)
        orig = client.get(reverse("app_transaction_detail", args=[t.journal.reference]))
        assert "REVERTED" in orig.content.decode()
        assert "View reversal" in orig.content.decode()
        rev_ref = t.journal.reversed_by.first().reference
        rpage = client.get(reverse("app_transaction_detail", args=[rev_ref])).content.decode()
        assert "View original operation" in rpage

    def test_original_preserved_after_reversal(self, client):
        from apps.transfers.services import reverse_transfer
        s = _user("dt-h"); sa = _account(s)
        r = _user("dt-i"); ra = _account(r, "50.00")
        t = self._transfer(sa, ra)
        reverse_transfer(t, actor=s)
        client.force_login(s)
        detail = client.get(reverse("app_transaction_detail", args=[t.journal.reference]))
        # original stays visible with its own reference — history not rewritten
        assert t.reference in detail.content.decode()

    def test_idor_foreign_detail_and_receipt_404(self, client):
        s = _user("dt-j"); sa = _account(s)
        outsider = _user("dt-k")
        r = _user("dt-l"); ra = _account(r, "50.00")
        t = self._transfer(sa, ra)
        client.force_login(outsider)
        ref = t.journal.reference
        assert client.get(reverse("app_transaction_detail", args=[ref])).status_code == 404
        assert client.get(reverse("app_transaction_receipt", args=[ref])).status_code == 404
        # tampered reference is equally 404
        assert client.get(reverse("app_transaction_detail", args=["NOPE-123"])).status_code == 404

    def test_views_are_read_only(self, client):
        from apps.ledger.models import JournalEntry
        s = _user("dt-m"); sa = _account(s)
        r = _user("dt-n"); ra = _account(r, "50.00")
        t = self._transfer(sa, ra)
        chain_before = JournalEntry.objects.get(pk=t.journal_id).chain_hash
        balance_before = float(sa.current_balance)
        client.force_login(s)
        client.get(reverse("app_transaction_detail", args=[t.journal.reference]))
        client.get(reverse("app_transaction_receipt", args=[t.journal.reference]))
        after = JournalEntry.objects.get(pk=t.journal_id)
        assert after.chain_hash == chain_before
        assert float(sa.current_balance) == balance_before
