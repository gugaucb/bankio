"""FASE 8 Branch 5 — billing cycle / statement closing tests."""
from datetime import date
from decimal import Decimal

import pytest

from apps.cards.billing import (close_card_statements,
                                open_cycle_total, statement_composition)
from apps.cards.models import CreditStatement

D = Decimal


def _customer(username):
    from apps.customers.models import Customer
    from tests.conftest import make_user
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _credit_card(username, credit_limit="1000.00"):
    from apps.accounts.models import Account
    from apps.cards.models import Card
    from apps.ledger.services import get_or_create_account
    u = _customer(username)
    la = get_or_create_account(f"2001-BC-{username}", f"A {username}", is_customer=True)
    a = Account.objects.create(customer=u, account_number=f"33{u.pk:010d}", ledger_account=la)
    return u, Card.objects.create(account=a, holder_name=username.upper(),
                                  type="CREDIT_CARD", credit_limit=D(credit_limit))


def _buy(card, merchant, amount, at):
    from unittest.mock import patch
    from apps.cards.services import purchase
    with patch("django.utils.timezone.now", return_value=at):
        return purchase(card_id=card.pk, merchant=merchant, amount_raw=D(amount),
                        idempotency_key=f"BC-{card.pk}-{merchant}-{at.date()}")


from django.utils import timezone
import datetime as dt


def _at(y, m, d):
    return dt.datetime(y, m, d, 12, 0, tzinfo=dt.timezone.utc)


@pytest.mark.django_db(transaction=True)
class TestBillingCycle:
    def test_purchase_enters_correct_cycle_and_closes(self):
        u, c = _credit_card("bc-a")
        _buy(c, "July Shop", "100.00", _at(2026, 7, 5))
        created = close_card_statements(reference=date(2026, 8, 1))
        assert len(created) == 1
        s = created[0]
        assert (s.period_start, s.period_end) == (date(2026, 7, 1), date(2026, 7, 31))
        assert s.amount_due == D("100.00")
        assert s.due_date == date(2026, 8, 10)

    def test_close_idempotent_never_duplicates(self):
        u, c = _credit_card("bc-b")
        _buy(c, "Shop", "40.00", _at(2026, 6, 10))
        first = close_card_statements(reference=date(2026, 7, 1))
        again = close_card_statements(reference=date(2026, 7, 1))
        third = close_card_statements(reference=date(2026, 7, 15))  # still same cycle
        assert len(first) == 1 and len(again) == 0 and len(third) == 0
        assert CreditStatement.objects.filter(card=c).count() == 1

    def test_purchase_after_closing_goes_to_next_cycle(self):
        u, c = _credit_card("bc-c")
        _buy(c, "Old", "30.00", _at(2026, 5, 20))
        close_card_statements(reference=date(2026, 6, 1))
        # late purchase dated AFTER the closed cycle: belongs to the open one
        _buy(c, "New", "70.00", _at(2026, 6, 3))
        stmts = CreditStatement.objects.filter(card=c)
        assert stmts.count() == 1
        assert stmts.first().amount_due == D("30.00")  # history immutable
        assert open_cycle_total(c, reference=date(2026, 6, 5)) == D("70.00")

    def test_zero_purchases_no_empty_invoice(self):
        _credit_card("bc-d")
        assert close_card_statements(reference=date(2026, 8, 1)) == []

    def test_multiple_cards_customers_independent(self):
        _, c1 = _credit_card("bc-e1")
        _, c2 = _credit_card("bc-e2")
        _buy(c1, "S1", "10.00", _at(2026, 4, 2))
        _buy(c2, "S2", "20.00", _at(2026, 4, 9))
        created = close_card_statements(reference=date(2026, 5, 1))
        amounts = {str(s.card.pk): s.amount_due for s in created}
        assert len(created) == 2
        assert set(amounts.values()) == {D("10.00"), D("20.00")}

    def test_declined_transactions_never_compose_invoice(self):
        u, c = _credit_card("bc-f")
        try:
            purchase(card_id=c.pk, merchant="Never", amount_raw=D("99999.00"),
                     idempotency_key=f"BC-F-{c.pk}")
        except Exception:
            pass
        assert close_card_statements(reference=date(2026, 8, 1)) == []

    def test_composition_explains_total(self):
        u, c = _credit_card("bc-g")
        _buy(c, "A", "10.00", _at(2026, 3, 2))
        _buy(c, "B", "15.00", _at(2026, 3, 28))
        s = close_card_statements(reference=date(2026, 4, 1))[0]
        lines = list(statement_composition(s))
        assert [t.merchant for t in lines] == ["A", "B"]
        assert sum(t.amount for t in lines) == s.amount_due == D("25.00")

    def test_command_runs_and_audits(self):
        from apps.audit.models import AuditLog
        from io import StringIO
        from django.core.management import call_command
        u, c = _credit_card("bc-h")
        _buy(c, "Cmd Shop", "55.00", _at(2026, 2, 11))
        out = StringIO()
        call_command("close_card_invoices", reference="2026-03-01", stdout=out)
        assert "Closed 1 statement(s)." in out.getvalue()
        assert AuditLog.objects.filter(action="CARD_INVOICE_CLOSED").exists()
