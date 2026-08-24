"""FASE 5 Branch 1 — Statement core service tests."""
from decimal import Decimal

import pytest
from django.core.paginator import Paginator
from django.utils import timezone

from apps.accounts.statement import (
    closing_balance_matches,
    get_owned_account,
    statement_lines,
    statement_queryset,
)
from apps.ledger.models import JournalEntry, LedgerEntry
from apps.ledger.services import account_balance, post_journal, reverse_journal

D = Decimal


@pytest.mark.django_db
class TestStatementCore:
    def _opening(self, account_factory):
        from tests.conftest import make_user
        from apps.customers.models import Customer
        u = make_user("stmt-user")
        Customer.objects.create(user=u, customer_number="CUST-ST")
        return account_factory(u, "1000.00")

    def test_empty_account(self, account_factory):
        from tests.conftest import make_user
        from apps.customers.models import Customer
        u = make_user("stmt-empty")
        Customer.objects.create(user=u, customer_number="CUST-SE")
        acct = account_factory(u, "0.00")
        lines = statement_lines(acct, statement_queryset(acct))
        assert lines == []
        assert closing_balance_matches(acct, None)

    def test_one_credit_is_IN_with_positive_direction(self, account_factory):
        acct = self._opening(account_factory)
        # opening balance itself is a CREDIT to the liability ledger account
        qs = statement_queryset(acct)
        lines = statement_lines(acct, qs)
        assert len(lines) == 1
        line = lines[0]
        assert line.direction == "IN"
        assert line.amount == D("1000.00")
        assert line.balance_after == D("1000.00")
        assert line.currency == "USD"

    def test_one_debit_is_OUT(self, account_factory):
        acct = self._opening(account_factory)
        other = acct.ledger_account
        from apps.ledger.services import get_or_create_account
        sink = get_or_create_account(f"6900-SINK-{acct.pk}", "Sink", type="EXPENSE")
        post_journal(reference=f"SINK-{acct.pk}", description="withdrawal",
                     lines=[(other, "DEBIT", D("250.00")), (sink, "CREDIT", D("250.00"))])
        lines = statement_lines(acct, statement_queryset(acct))
        out = [l for l in lines if l.direction == "OUT"]
        assert len(out) == 1 and out[0].amount == D("250.00")
        assert out[0].balance_after == D("750.00")

    def test_ordering_and_running_balance(self, account_factory):
        acct = self._opening(account_factory)
        from apps.ledger.services import get_or_create_account
        la = acct.ledger_account
        sink = get_or_create_account(f"6900-SINK-{acct.pk}", "Sink", type="EXPENSE")
        src = get_or_create_account(f"1000-SRC-{acct.pk}", "Src", type="ASSET")
        same_ts = timezone.now()
        post_journal(reference=f"A-{acct.pk}", description="in +300",
                     lines=[(src, "DEBIT", D("300.00")), (la, "CREDIT", D("300.00"))],
                     posted_at=same_ts)
        post_journal(reference=f"B-{acct.pk}", description="out -100",
                     lines=[(la, "DEBIT", D("100.00")), (sink, "CREDIT", D("100.00"))],
                     posted_at=same_ts)
        lines = statement_lines(acct, statement_queryset(acct))
        refs = [l.operation_reference for l in lines]
        # opening first, then deterministic tie-break by journal id on equal timestamp
        assert refs == [f"OPEN-{acct.account_number}", f"A-{acct.pk}", f"B-{acct.pk}"]
        balances = [l.balance_after for l in lines]
        assert balances == [D("1000.00"), D("1300.00"), D("1200.00")]
        assert closing_balance_matches(acct, balances[-1])

    def test_final_balance_matches_balance_service_many(self, account_factory):
        acct = self._opening(account_factory)
        la = acct.ledger_account
        from apps.ledger.services import get_or_create_account
        sink = get_or_create_account(f"6900-SINK-{acct.pk}", "Sink", type="EXPENSE")
        for i in range(7):
            amt = D(f"{50 * (i + 1)}.00")
            lines = ([(la, "DEBIT", amt), (sink, "CREDIT", amt)] if i % 2
                     else [(sink, "DEBIT", amt), (la, "CREDIT", amt)])
            post_journal(reference=f"M{i}-{acct.pk}", description="m", lines=lines)
        lines = statement_lines(acct, statement_queryset(acct))
        assert closing_balance_matches(acct, lines[-1].balance_after)
        assert account_balance(la) == lines[-1].balance_after

    def test_draft_journal_excluded(self, account_factory):
        acct = self._opening(account_factory)
        j = JournalEntry.objects.create(reference=f"DRAFT-{acct.pk}", description="d", status="DRAFT")
        LedgerEntry.objects.create(journal=j, account=acct.ledger_account, side="CREDIT", amount=D("999.00"))
        lines = statement_lines(acct, statement_queryset(acct))
        assert all(l.status == "POSTED" for l in lines)
        assert len(lines) == 1  # only the opening credit

    def test_failed_transfer_no_movement(self, alice, bob):
        from apps.transfers.services import execute_transfer
        with pytest.raises(Exception):
            execute_transfer(actor=alice.checking.customer,
                             source_account_id=alice.checking.pk, amount=D("999999"),
                             destination_account_id=bob.checking.pk)
        lines = statement_lines(alice.checking, statement_queryset(alice.checking))
        assert len(lines) == 1  # opening only; risk block = zero movement

    def test_reversal_keeps_both_lines(self, account_factory):
        acct = self._opening(account_factory)
        la = acct.ledger_account
        from apps.ledger.services import get_or_create_account
        sink = get_or_create_account(f"6900-SINK-{acct.pk}", "Sink", type="EXPENSE")
        j = post_journal(reference=f"REVME-{acct.pk}", description="out",
                         lines=[(la, "DEBIT", D("200.00")), (sink, "CREDIT", D("200.00"))])
        reverse_journal(j)
        lines = statement_lines(acct, statement_queryset(acct))
        outs_ins = [(l.direction, l.amount) for l in lines[1:]]
        assert ("OUT", D("200.00")) in outs_ins and ("IN", D("200.00")) in outs_ins
        assert closing_balance_matches(acct, lines[-1].balance_after)

    def test_cross_currency_journal_excluded(self, account_factory):
        acct = self._opening(account_factory)
        from apps.ledger.services import get_or_create_account
        eur_la = get_or_create_account(f"2001-EUR-{acct.pk}", "EUR acc", is_customer=True)
        # EUR journal touching a different ledger never appears; also currency filter guard:
        src = get_or_create_account(f"1000-EURSRC-{acct.pk}", "S", type="ASSET")
        post_journal(reference=f"EUR-{acct.pk}", description="eur",
                     lines=[(src, "DEBIT", D("10.00")), (eur_la, "CREDIT", D("10.00"))])
        lines = statement_lines(acct, statement_queryset(acct))
        assert {l.currency for l in lines} == {"USD"}

    def test_idor_get_owned_account(self, account_factory):
        from tests.conftest import make_user
        from apps.customers.models import Customer
        a = make_user("idor-a"); Customer.objects.create(user=a, customer_number="CUST-IA")
        b = make_user("idor-b"); Customer.objects.create(user=b, customer_number="CUST-IB")
        acct_a = account_factory(a, "10.00")
        acct_b = account_factory(b, "20.00")
        assert get_owned_account(a, acct_a.pk).pk == acct_a.pk
        with pytest.raises(acct_b.__class__.DoesNotExist):
            get_owned_account(a, acct_b.pk)

    def test_pagination_stable(self, account_factory):
        acct = self._opening(account_factory)
        la = acct.ledger_account
        from apps.ledger.services import get_or_create_account
        sink = get_or_create_account(f"6900-SINK-{acct.pk}", "Sink", type="EXPENSE")
        base = timezone.now() - timezone.timedelta(minutes=60)
        for i in range(29):
            post_journal(reference=f"P{i}-{acct.pk}", description="p",
                         lines=[(la, "DEBIT", D("1.00")), (sink, "CREDIT", D("1.00"))],
                         posted_at=base + timezone.timedelta(seconds=i))
        page = Paginator(statement_queryset(acct), 25)
        p1 = statement_lines(acct, page.get_page(1))
        p2 = statement_lines(acct, page.get_page(2))
        assert len(p1) == 25 and len(p2) == 5
        assert page.num_pages == 2
        # stable across requests
        assert [l.journal_id for l in p1] == [l.journal_id for l in statement_lines(acct, page.get_page(1))]

    def test_transfer_replay_no_duplicate_line(self, alice, bob):
        from django.conf import settings
        from apps.transfers.services import execute_transfer
        t1, c1 = execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                                  amount=D("10.00"), destination_account_id=bob.checking.pk,
                                  idempotency_key="STMT-REPLAY-1")
        t2, c2 = execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                                  amount=D("10.00"), destination_account_id=bob.checking.pk,
                                  idempotency_key="STMT-REPLAY-1")
        assert not c2 and t1.pk == t2.pk
        lines = statement_lines(alice.checking, statement_queryset(alice.checking))
        outs = [l for l in lines if l.direction == "OUT"]
        assert len(outs) == 1 and outs[0].amount == D("10.00")

    def test_projection_query_count_constant(self, account_factory, django_assert_num_queries):
        acct = self._opening(account_factory)
        la = acct.ledger_account
        from apps.ledger.services import get_or_create_account
        sink = get_or_create_account(f"6900-SINK-{acct.pk}", "Sink", type="EXPENSE")
        for i in range(30):
            post_journal(reference=f"Q{i}-{acct.pk}", description="q",
                         lines=[(la, "DEBIT", D("1.00")), (sink, "CREDIT", D("1.00"))])
        page = list(Paginator(statement_queryset(acct), 25).get_page(1))
        # batched projection must stay flat: queryset + 3 batched source lookups,
        # independent of row count
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as ctx:
            statement_lines(acct, page)
        assert len(ctx.captured_queries) == 3, [q["sql"][:80] for q in ctx.captured_queries]
