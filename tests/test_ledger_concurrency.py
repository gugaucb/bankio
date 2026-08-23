"""Concurrency safety proven against real PostgreSQL with separate
connections per thread — not mocks."""
import threading
from decimal import Decimal

import pytest

from apps.cards import services as card_services
from apps.cards.models import Card, CardStatus
from apps.lending import services as lending
from apps.lending.models import LoanProduct, RepaymentSchedule
from apps.ledger.models import JournalEntry


def make_customer_user(username, opening="5000.00"):
    from django.contrib.auth import get_user_model

    from apps.accounts.models import Account
    from apps.customers.models import Customer
    from apps.ledger import services as ledger

    User = get_user_model()
    u = User.objects.create_user(
        username=username, email=f"{username}@t.io", password="Test!12345",
        role="CUSTOMER", first_name=username.capitalize(),
    )
    Customer.objects.create(user=u, customer_number=f"CUST-{username.upper()}"[:16])
    la = ledger.get_or_create_account(f"2001-{username[:26]}", f"Account {username}", is_customer=True)
    acct = Account.objects.create(
        customer=u, account_number=username[:16].rjust(16, "9"), ledger_account=la
    )
    u.checking = acct
    equity = ledger.get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
    ledger.post_journal(
        reference=f"OPEN-{acct.account_number}",
        description="opening balance",
        lines=[(equity, "DEBIT", opening), (la, "CREDIT", opening)],
    )
    return u


def run_threads(target, n):
    threads = [threading.Thread(target=target) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


@pytest.mark.django_db(transaction=True)
def test_concurrent_card_purchases_cannot_overdraw():
    """$100 balance, two concurrent $80 purchases: exactly one succeeds."""
    from django.db import connections

    user = make_customer_user("cc_buyer", opening="100.00")
    card = Card.objects.create(
        account=user.checking, type="DEBIT_CARD", status=CardStatus.ACTIVE,
        holder_name="CC BUYER",
    )
    results = []

    def do():
        try:
            tx = card_services.purchase(card.id, "Store", "80.00",
                                        idempotency_key=f"P-{threading.get_ident()}")
            results.append(("OK", tx.pk))
        except card_services.CardDeclined:
            results.append(("DECLINED",))
        finally:
            connections.close_all()

    run_threads(do, 2)
    assert sorted(r[0] for r in results) == ["DECLINED", "OK"]
    user.checking.refresh_from_db()
    assert user.checking.current_balance == Decimal("20.00")
    assert JournalEntry.objects.filter(reference__startswith="CDT-").count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_same_key_purchase_single_movement():
    from django.db import connections

    user = make_customer_user("cc_idem")
    card = Card.objects.create(
        account=user.checking, type="DEBIT_CARD", status=CardStatus.ACTIVE,
        holder_name="CC IDEM",
    )
    refs = []

    def do():
        try:
            tx = card_services.purchase(card.id, "Store", "30.00", idempotency_key="ONE-KEY")
            refs.append(tx.journal_id)
        finally:
            connections.close_all()

    run_threads(do, 3)
    assert len(refs) == 3
    assert len(set(refs)) == 1  # same journal -> one movement
    assert JournalEntry.objects.filter(reference__startswith="CDT-").count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_repay_installment_pays_once():
    from django.db import connections

    user = make_customer_user("loan_repayer")
    product, _ = LoanProduct.objects.get_or_create(
        code="CONC-LOAN",
        defaults={"name": "Conc", "type": "PERSONAL", "min_amount": "100",
                  "max_amount": "10000", "base_rate": "5"},
    )
    app = lending.apply_for_loan(
        customer=user, product=product, amount="600", term_months=6,
        disbursed_account=user.checking,
    )
    app.status = "APPROVED"
    app.save(update_fields=["status"])
    lending.disburse(app)
    sched = app.schedule.first()
    results = []

    def do():
        try:
            s = lending.repay_installment(sched, user)
            results.append(("OK", s.pk))
        except ValueError:
            results.append(("ERR",))
        finally:
            connections.close_all()

    run_threads(do, 2)
    # both calls may report OK, but only ONE financial movement may exist
    assert JournalEntry.objects.filter(reference__startswith="LRP-").count() == 1
    sched.refresh_from_db()
    assert sched.paid_at is not None
