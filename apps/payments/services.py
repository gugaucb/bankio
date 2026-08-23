"""Payments: bills, invoices, subscriptions — all settled through the ledger."""
import uuid
from decimal import Decimal

from django.db import transaction

from apps.accounts.models import Account, AccountStatus
from apps.ledger import services as ledger

from .models import Bill, Payment


class PaymentError(Exception):
    pass


@transaction.atomic
def pay_bill(*, actor, account_id, bill_id, idempotency_key=None):
    key = idempotency_key or str(uuid.uuid4())
    existing = Payment.objects.filter(idempotency_key=key).first()
    if existing:
        return existing, False

    account = Account.objects.select_for_update().get(pk=account_id)
    if actor.is_customer and account.customer_id != actor.id:
        raise PaymentError("FORBIDDEN")
    if account.status != AccountStatus.ACTIVE:
        raise PaymentError("ACCOUNT_NOT_ACTIVE")

    bill = Bill.objects.select_for_update().get(pk=bill_id)
    amount = bill.amount.quantize(Decimal("0.01"))
    if account.available_balance < amount:
        raise PaymentError("INSUFFICIENT_FUNDS")
    if bill.payments.filter(status="COMPLETED").exists():
        raise PaymentError("BILL_ALREADY_PAID")

    bank_income = ledger.get_or_create_account("4000-PAYMENT-INCOME", "Payment Settlement Clearing", type="ASSET")
    journal = ledger.post_journal(
        reference=f"PAY-{uuid.uuid4().hex[:12].upper()}",
        description=f"Bill payment {bill.biller}",
        lines=[(account.ledger_account, "DEBIT", amount), (bank_income, "CREDIT", amount)],
    )
    payment = Payment.objects.create(
        account=account, bill=bill, amount=amount,
        idempotency_key=key, journal=journal, created_by=actor,
    )
    return payment, True
