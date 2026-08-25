"""FASE 8 Branch 3 — limits & derived availability tests."""
from decimal import Decimal

import pytest

from apps.cards.services import (CardDeclined, credit_availability,
                                 credit_used, purchase)

D = Decimal


def _customer(username):
    from apps.customers.models import Customer
    from tests.conftest import make_user
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


def _card(username, credit_limit="500.00", **kw):
    from apps.accounts.models import Account
    from apps.cards.models import Card
    from apps.ledger.services import get_or_create_account
    u = _customer(username)
    la = get_or_create_account(f"2001-LM-{username}", f"A {username}", is_customer=True)
    a = Account.objects.create(customer=u, account_number=f"55{u.pk:010d}", ledger_account=la)
    return u, Card.objects.create(account=a, holder_name=username.upper(),
                                  type="CREDIT_CARD",
                                  credit_limit=D(credit_limit), **kw)


@pytest.mark.django_db(transaction=True)
class TestDerivedAvailability:
    def test_purchase_reduces_availability_exactly_once(self):
        u, c = _card("lm-a", "500.00")
        purchase(card_id=c.pk, merchant="LM A", amount_raw=D("100.00"),
                 idempotency_key=f"LM-A1-{c.pk}")
        used, available = credit_availability(c)
        assert (used, available) == (D("100.00"), D("400.00"))
        # idempotent replay does NOT reduce again
        purchase(card_id=c.pk, merchant="LM A", amount_raw=D("100.00"),
                 idempotency_key=f"LM-A1-{c.pk}")
        used2, available2 = credit_availability(c)
        assert (used2, available2) == (D("100.00"), D("400.00"))

    def test_decline_changes_nothing(self):
        u, c = _card("lm-b", "100.00")
        before = credit_availability(c)
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="LM B", amount_raw=D("150.00"),
                     idempotency_key=f"LM-B1-{c.pk}")  # above tx? no: above limit
        assert credit_availability(c) == before

    def test_exactly_at_limit_ok_above_declines(self):
        u, c = _card("lm-c", "100.00")
        tx = purchase(card_id=c.pk, merchant="LM C1", amount_raw=D("100.00"),
                      idempotency_key=f"LM-C1-{c.pk}")
        assert not tx.declined
        assert credit_availability(c)[1] == D("0.00")
        with pytest.raises(CardDeclined):
            purchase(card_id=c.pk, merchant="LM C2", amount_raw=D("0.01"),
                     idempotency_key=f"LM-C2-{c.pk}")

    def test_never_negative(self):
        u, c = _card("lm-d", "50.00")
        purchase(card_id=c.pk, merchant="LM D", amount_raw=D("50.00"),
                 idempotency_key=f"LM-D1-{c.pk}")
        used, available = credit_availability(c)
        assert available >= 0 and used <= c.credit_limit

    def test_settled_statement_restores_availability(self):
        from apps.cards.models import CreditStatement
        from apps.cards.services import pay_statement
        u, c = _card("lm-e", "100.00")
        from apps.ledger.services import get_or_create_account, post_journal
        equity = get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        post_journal(reference=f"OPEN-LM-E-{c.pk}", description="opening",
                     lines=[(equity, "DEBIT", D("200.00")),
                            (c.account.ledger_account, "CREDIT", D("200.00"))])
        purchase(card_id=c.pk, merchant="LM E", amount_raw=D("80.00"),
                 idempotency_key=f"LM-E1-{c.pk}")
        assert credit_availability(c)[1] == D("20.00")
        stmt = CreditStatement.objects.create(
            card=c, period_start="2026-07-01", period_end="2026-07-31",
            amount_due=D("80.00"))
        pay_statement(actor=u, card_id=c.pk,
                      idempotency_key=f"LM-EPAY-{c.pk}")
        used, available = credit_availability(c)
        assert used == D("0.00") and available == D("100.00")
        # replay of payment does not restore twice / change anything
        total_again = pay_statement(actor=u, card_id=c.pk,
                                    idempotency_key=f"LM-EPAY-{c.pk}")
        assert str(total_again) == "80.00"
        assert credit_used(c) == D("0.00")


@pytest.mark.django_db(transaction=True)
class TestConcurrency:
    def test_concurrent_purchases_cannot_overspend_credit(self):
        """Two concurrent purchases against one small limit: at most one may
        settle — the card lock inside _purchase_atomic serializes checks."""
        import threading
        u, c = _card("lm-race", "100.00")
        results = []

        def buy(key):
            try:
                tx = purchase(card_id=c.pk, merchant=f"LM R{key}",
                              amount_raw=D("70.00"),
                              idempotency_key=f"LM-R{key}-{c.pk}")
                results.append(tx.declined)
            except CardDeclined:
                results.append("declined")

        t1 = threading.Thread(target=buy, args=(1,))
        t2 = threading.Thread(target=buy, args=(2,))
        t1.start(); t2.start(); t1.join(); t2.join()
        approved = [r for r in results if r is False]
        assert len(approved) <= 1  # 70+70 > 100: never both settle
        used, _avail = credit_availability(c)
        assert used <= D("100.00")


@pytest.mark.django_db
class TestLimitChangeAuthority:
    def test_customer_cannot_set_credit_limit_via_controls(self):
        from apps.cards.services import set_card_control
        u, c = _card("lm-auth")
        with pytest.raises(ValueError):  # credit_limit not an allowed control
            set_card_control(u, c.pk, credit_limit=D("999999.00"))
        c.refresh_from_db()
        assert c.credit_limit != D("999999.00")

    def test_customer_ui_cannot_inject_limit(self, client):
        u, c = _card("lm-mass")
        client.force_login(u)
        client.post(f"/app/cards/{c.pk}/controls/", {
            "action": "toggle_online", "credit_limit": "999999",
            "tx_limit": "999999"})
        c.refresh_from_db()
        assert str(c.credit_limit) == "500.00"
