"""Payments, lending and investments domain tests."""
from decimal import Decimal

import pytest

from apps.investments.models import Instrument
from apps.investments.services import TradingError, place_order
from apps.lending.models import LoanApplication, LoanProduct
from apps.lending.services import apply_for_loan, approve, credit_score, monthly_payment, repay_installment, simulate
from apps.payments.models import Bill
from apps.payments.services import PaymentError, pay_bill


# ---------- payments ----------

def test_pay_bill(alice):
    bill = Bill.objects.create(biller="City Power", amount="120.50")
    p, created = pay_bill(actor=alice, account_id=alice.checking.pk, bill_id=bill.pk)
    assert created and p.status == "COMPLETED"
    assert alice.checking.current_balance == Decimal("879.50")
    with pytest.raises(PaymentError):  # second payment of same bill blocked
        pay_bill(actor=alice, account_id=alice.checking.pk, bill_id=bill.pk)


def test_pay_bill_idempotent(alice):
    bill = Bill.objects.create(biller="Water Co", amount="40.00")
    a = dict(actor=alice, account_id=alice.checking.pk, bill_id=bill.pk, idempotency_key="PB-1")
    p1, c1 = pay_bill(**a)
    p2, c2 = pay_bill(**a)
    assert c1 and not c2 and p1.pk == p2.pk
    assert alice.checking.current_balance == Decimal("960.00")


def test_insufficient_bill_payment(alice):
    bill = Bill.objects.create(biller="Expensive", amount="5000.00")
    with pytest.raises(PaymentError) as e:
        pay_bill(actor=alice, account_id=alice.checking.pk, bill_id=bill.pk)
    assert e.value.args[0] == "INSUFFICIENT_FUNDS"


def test_other_users_account_forbidden(alice, bob):
    bill = Bill.objects.create(biller="X", amount="5.00")
    with pytest.raises(PaymentError) as e:
        pay_bill(actor=bob, account_id=alice.checking.pk, bill_id=bill.pk)
    assert e.value.args[0] == "FORBIDDEN"


# ---------- lending ----------

def test_monthly_payment_math():
    # known PMT: $10k @ 12% / 12 months ≈ 888.49
    assert monthly_payment("10000", "12", 12) == Decimal("888.49")
    s = simulate("10000", "12", 12)
    assert s["total"] > Decimal("10000")


def test_loan_flow(alice):
    product = LoanProduct.objects.create(code="T-PERSONAL", name="Personal T", type="PERSONAL",
                                         min_amount="1000", max_amount="10000")
    app = apply_for_loan(customer=alice, product=product, amount="5000", term_months=12,
                         disbursed_account=alice.checking)
    assert app.status == "REVIEW" and app.score is not None
    before = alice.checking.current_balance
    approve(app, manager=None)
    assert app.status == "ACTIVE"
    assert app.schedule.count() == 12
    assert alice.checking.current_balance == before + Decimal("5000")

    first = app.schedule.first()
    repay_installment(first, actor=alice)
    assert first.paid_at is not None
    assert alice.checking.current_balance < before + Decimal("5000")


def test_low_score_rejected(user_factory):
    from apps.accounts.models import Account

    u = user_factory("poor")
    product = LoanProduct.objects.get_or_create(code="T2", defaults=dict(name="P", type="PERSONAL"))[0]
    app = LoanApplication.objects.create(customer=u, product=product, amount="99999",
                                         term_months=6, interest_rate="10", status="REVIEW", score=400)
    approve(app, manager=None)
    assert app.status == "REJECTED"


# ---------- investments ----------

@pytest.fixture
def instrument(db):
    return Instrument.objects.create(symbol="TEST", name="Test Corp", last_price="10.00")


def test_buy_and_sell_roundtrip(alice, bob, instrument):
    order, created = place_order(actor=alice, account_id=alice.checking.pk,
                                 symbol="TEST", side="BUY", quantity="10")
    assert created
    assert alice.checking.current_balance == Decimal("900.00")
    place_order(actor=alice, account_id=alice.checking.pk, symbol="TEST", side="SELL", quantity="4")
    assert alice.checking.current_balance == Decimal("940.00")


def test_oversell_rejected(alice, instrument):
    with pytest.raises(TradingError) as e:
        place_order(actor=alice, account_id=alice.checking.pk, symbol="TEST", side="SELL", quantity="1")
    assert e.value.args[0] == "INSUFFICIENT_POSITION"


def test_insufficient_funds_buy(alice, instrument):
    with pytest.raises(TradingError) as e:
        place_order(actor=alice, account_id=alice.checking.pk, symbol="TEST", side="BUY", quantity="500")
    assert e.value.args[0] == "INSUFFICIENT_FUNDS"


def test_unknown_symbol(alice):
    with pytest.raises(TradingError) as e:
        place_order(actor=alice, account_id=alice.checking.pk, symbol="NOPE", side="BUY", quantity="1")
    assert e.value.args[0] == "UNKNOWN_INSTRUMENT"
