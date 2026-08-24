"""Payments: bills, invoices, subscriptions — all settled through the ledger."""
import uuid
from decimal import Decimal

from django.db import transaction

from apps.accounts.models import Account, AccountStatus
from apps.ledger import services as ledger

from .models import Bill, Payment


class PaymentError(Exception):
    pass


def pay_bill(*, actor, account_id, bill_id, idempotency_key=None,
             step_up_code=None, step_up_challenge_id=None):
    """Public entry: the risk gate runs OUTSIDE the settlement transaction so
    the evaluation snapshot survives an aborted payment (INV 9).

    A presented step-up code satisfies a pending bound challenge exactly once
    (locked verify+consume) before settlement; without it, CHALLENGE stops the
    flow with STEP_UP_REQUIRED and a challenge is issued + delivered.
    """
    from apps.fraud import modes
    from apps.fraud.challenge import ChallengeError
    from apps.fraud.challenge_guard import confirm
    from apps.fraud.challenge_delivery import issue_and_deliver

    key = idempotency_key or str(uuid.uuid4())
    existing = Payment.objects.filter(idempotency_key=key).first()
    if existing:
        return existing, False

    account = Account.objects.select_related("customer").get(pk=account_id)
    bill = Bill.objects.get(pk=bill_id)
    ev = _payment_risk_observation(actor, account, bill.amount, bill, key)

    facts = {
        "amount": str(bill.amount), "bill": str(bill.pk),
        "account": str(account.pk), "idempotency_key": key,
    }
    if ev is not None and modes.effective_decision(ev) == "CHALLENGE":
        if step_up_code and step_up_challenge_id:
            try:
                confirm(step_up_challenge_id, account.customer, step_up_code,
                        facts, f"BILL_PAYMENT:{key}", actor=actor)
            except ChallengeError as exc:
                raise PaymentError(str(exc))
            # verified & consumed → settlement proceeds below, once per key
        else:
            ch, _code = issue_and_deliver(ev, account.customer, facts, actor=actor)
            err = PaymentError("STEP_UP_REQUIRED")
            err.challenge_id = ch.pk
            err.facts = facts
            raise err
    elif ev is not None:
        from apps.fraud.gate import RiskGateIntervention, enforce

        try:
            enforce(ev)
        except RiskGateIntervention as g:
            raise PaymentError(g.action)

    return _pay_bill_atomic(actor=actor, account_id=account_id, bill_id=bill_id,
                            idempotency_key=key)


def resume_payment(*, actor, challenge_id, code, facts):
    """Resume a STEP_UP_REQUIRED bill payment with its exact original facts.

    Facts are re-validated against the material hash inside the gate;
    settlement happens at most once via the idempotency_key."""
    return pay_bill(actor=actor, account_id=int(facts["account"]),
                    bill_id=int(facts["bill"]), idempotency_key=facts["idempotency_key"],
                    step_up_code=code, step_up_challenge_id=challenge_id)


@transaction.atomic
def _pay_bill_atomic(*, actor, account_id, bill_id, idempotency_key):
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
        idempotency_key=idempotency_key, journal=journal, created_by=actor,
    )
    transaction.on_commit(lambda: _payment_completed_notification(payment.pk))
    return payment, True


def _payment_completed_notification(payment_id):
    # FASE 6: customer notification AFTER the settlement commit only.
    from apps.notifications.services import notify

    p = Payment.objects.select_related("bill", "account__customer").filter(
        pk=payment_id).first()
    if p is None:
        return
    notify(recipient=p.created_by, category="PAYMENT", kind="PAYMENT_COMPLETED",
           title="Payment completed",
           body=(f"Your payment of ${p.amount} to {p.bill.biller} was completed "
                 f"(ref {p.journal.reference})."),
           metadata={"reference": p.journal.reference},
           dedup_key=f"PAYMENT_COMPLETED:{p.idempotency_key}:{p.created_by_id}")


def _payment_risk_observation(actor, account, amount, bill, idempotency_key):
    """Run the fraud engine on the payment; never fatal in SHADOW."""
    from apps.fraud.context import RiskContext
    from apps.fraud.engine import evaluate_operation

    ctx = RiskContext(
        operation_type="BILL_PAYMENT",
        actor=actor,
        customer=account.customer,
        amount=amount,
        currency=account.currency,
        account_ref=str(account.pk),
        idempotency_key=idempotency_key,
    )
    try:
        return evaluate_operation(ctx)
    except Exception as exc:
        from apps.audit.services import record as audit

        audit(action="RISK_EVALUATION_ERROR", metadata={"scope": "bill_payment", "error": str(exc)[:200]})
        return None
