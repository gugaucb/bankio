"""Idempotency: duplicate financial requests must produce one movement."""
from decimal import Decimal

import pytest

from apps.cards import services as card_services
from apps.cards.models import Card, CardStatus, CreditStatement
from apps.lending import services as lending
from apps.lending.models import LoanProduct, RepaymentSchedule
from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry


def make_customer_user(factory, username):
    from apps.customers.models import Customer

    u = factory(username)
    Customer.objects.create(user=u, customer_number=f"CUST-{username.upper()}")
    return u


@pytest.fixture
def funded_user(user_factory, account_factory):
    u = make_customer_user(user_factory, "idem-user")
    u.checking = account_factory(u, "5000.00")
    return u


@pytest.fixture
def debit_card(funded_user):
    return Card.objects.create(
        account=funded_user.checking, type="DEBIT_CARD",
        status=CardStatus.ACTIVE, holder_name="IDEM USER",
    )


def test_duplicate_card_purchase_single_journal(debit_card):
    tx1 = card_services.purchase(debit_card.id, "Store", "10.00", idempotency_key="PUR-1")
    tx2 = card_services.purchase(debit_card.id, "Store", "10.00", idempotency_key="PUR-1")
    assert tx1.pk == tx2.pk
    assert JournalEntry.objects.filter(reference__startswith="CDT-").count() == 1
    assert ledger.account_balance(debit_card.account.ledger_account) == Decimal("4990.00")


def test_different_keys_are_separate_purchases(debit_card):
    card_services.purchase(debit_card.id, "Store", "10.00", idempotency_key="A")
    card_services.purchase(debit_card.id, "Store", "10.00", idempotency_key="B")
    assert JournalEntry.objects.filter(reference__startswith="CDT-").count() == 2


def test_duplicate_statement_payment_single_journal(funded_user, debit_card):
    CreditStatement.objects.create(
        card=debit_card, period_start="2026-07-01", period_end="2026-07-31",
        amount_due=Decimal("100.00"),
    )
    total1 = card_services.pay_statement(funded_user, debit_card.id, idempotency_key="ST-1")
    total2 = card_services.pay_statement(funded_user, debit_card.id, idempotency_key="ST-1")
    assert total1 == total2 == Decimal("100.00")
    assert JournalEntry.objects.filter(reference__startswith="STMT-").count() == 1
    assert ledger.account_balance(funded_user.checking.ledger_account) == Decimal("4900.00")


def _mk_application(customer, account):
    product, _ = LoanProduct.objects.get_or_create(
        code="IDEM-LOAN",
        defaults={"name": "Idem Loan", "type": "PERSONAL",
                  "min_amount": "100", "max_amount": "10000", "base_rate": "5"},
    )
    app = lending.apply_for_loan(
        customer=customer, product=product, amount="1000", term_months=6,
        disbursed_account=account,
    )
    app.status = "APPROVED"
    app.save(update_fields=["status"])
    return app


def test_double_disburse_single_journal(funded_user):
    app = _mk_application(funded_user, funded_user.checking)
    out1 = lending.disburse(app)
    out2 = lending.disburse(app)
    assert out1.pk == out2.pk
    assert JournalEntry.objects.filter(reference__startswith="LOAN-").count() == 1
    # balance = opening 5000 + one disbursement of 1000
    assert ledger.account_balance(funded_user.checking.ledger_account) == Decimal("6000.00")
    assert RepaymentSchedule.objects.filter(application=out1).count() == 6


def test_double_repayment_single_journal(funded_user):
    app = _mk_application(funded_user, funded_user.checking)
    lending.disburse(app)
    sched = app.schedule.first()
    s1 = lending.repay_installment(sched, funded_user, idempotency_key="RP-1")
    s2 = lending.repay_installment(sched, funded_user, idempotency_key="RP-1")
    assert s1.pk == s2.pk
    assert JournalEntry.objects.filter(reference__startswith="LRP-").count() == 1
