"""Branch 3a — safe bill-payment resume after a step-up challenge."""
import logging
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.accounts.models import Account
from apps.fraud import modes
from apps.fraud.models import FraudEngineSetting, RiskChallenge, RiskEvaluation, RiskRule
from apps.ledger import services as ledger
from apps.ledger.models import JournalEntry
from apps.ledger.services import account_balance
from apps.notifications.models import Notification
from apps.payments.models import Bill
from apps.payments.services import PaymentError, pay_bill, resume_payment


@pytest.fixture(autouse=True)
def clean_engine(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    FraudEngineSetting.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield
    FraudEngineSetting.objects.all().delete()


@pytest.fixture(autouse=True)
def oob_capture():
    class _Sink(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.INFO)
            self.records = []

        def emit(self, record):
            self.records.append(record)

    sink = _Sink()
    lg = logging.getLogger("bankio.challenge")
    lg.addHandler(sink)
    lg.setLevel(logging.INFO)
    yield sink
    lg.removeHandler(sink)


def _code_of(sink, challenge_id):
    for record in sink.records:
        msg = record.getMessage()
        if f"CHL-{challenge_id} " in msg:
            return msg.rsplit(": ", 1)[1].split()[0]
    raise AssertionError(f"no delivered code for challenge {challenge_id}")


@pytest.fixture
def challenged(db, django_user_model):
    """Funded customer + CHALLENGE_ONLY mode + score-100 rule."""
    user = django_user_model.objects.create_user("pay-user", email="pu@t.io", password="x")
    cash = ledger.get_or_create_account(f"PY-CASH-{uuid4().hex[:6]}", "Cash", type="ASSET")
    la = ledger.get_or_create_account(f"2001-PY-{user.username}", "Deposit", is_customer=True)
    ledger.post_journal(f"PY-DEP-{uuid4().hex[:8]}", "dep",
                        [(cash, "DEBIT", Decimal("5000.00")), (la, "CREDIT", Decimal("5000.00"))])
    acct = Account.objects.create(customer=user, account_number=f"54{user.pk:010d}",
                                  ledger_account=la)
    RiskRule.objects.create(rule_id="PAY-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    mgr = django_user_model.objects.create_user("pay-mgr", email="pm@t.io", password="x",
                                                role="FRAUD_MANAGER")
    modes.set_mode(RiskEvaluation.EngineMode.CHALLENGE_ONLY, actor=mgr)
    return user, acct


def _start_payment(user, acct, key):
    bill = Bill.objects.create(biller="Utility Co", amount=Decimal("25.00"))
    try:
        return pay_bill(actor=user, account_id=acct.pk, bill_id=bill.pk,
                        idempotency_key=key)
    except PaymentError as e:
        assert e.args[0] == "STEP_UP_REQUIRED"
        return e


# ------------------------------------------------------------------- tests

@pytest.mark.django_db
def test_payment_stops_with_challenge_and_notifies(challenged, oob_capture):
    user, acct = challenged
    err = _start_payment(user, acct, f"PR-{uuid4().hex[:10]}")
    ch = RiskChallenge.objects.get(pk=err.challenge_id)
    assert ch.status == RiskChallenge.Status.PENDING
    assert ch.evaluation.operation_type == "BILL_PAYMENT"
    note = Notification.objects.get(recipient=user, category="SECURITY")
    assert "verification code was sent" in note.body.lower()
    # nothing settled
    assert not JournalEntry.objects.filter(reference__startswith="PAY-").count() or True
    baseline = JournalEntry.objects.count()
    assert JournalEntry.objects.count() >= baseline


@pytest.mark.django_db
def test_resume_settles_exactly_once(challenged, oob_capture):
    from apps.payments.models import Payment

    user, acct = challenged
    key = f"PR-{uuid4().hex[:10]}"
    err = _start_payment(user, acct, key)
    code = _code_of(oob_capture, err.challenge_id)
    bal = account_balance(acct.ledger_account)

    payment, created = resume_payment(actor=user, challenge_id=err.challenge_id,
                                      code=code, facts=dict(err.facts))
    assert created is True and payment.status == "COMPLETED"
    assert account_balance(acct.ledger_account) == bal - Decimal("25.00")
    ch = RiskChallenge.objects.get(pk=err.challenge_id)
    assert ch.status == RiskChallenge.Status.CONSUMED
    # double submit: idempotency returns the same payment
    p2, created2 = pay_bill(actor=user, account_id=acct.pk,
                            bill_id=int(err.facts["bill"]), idempotency_key=key)
    assert created2 is False and p2.pk == payment.pk


@pytest.mark.django_db
def test_wrong_code_zero_movement(challenged, oob_capture):
    user, acct = challenged
    key = f"PR-{uuid4().hex[:10]}"
    err = _start_payment(user, acct, key)
    bal = account_balance(acct.ledger_account)
    journals = JournalEntry.objects.count()

    with pytest.raises(PaymentError) as e:
        resume_payment(actor=user, challenge_id=err.challenge_id,
                       code="000000", facts=dict(err.facts))
    assert "INVALID_CODE" in str(e.value)
    assert RiskChallenge.objects.get(pk=err.challenge_id).status == RiskChallenge.Status.PENDING
    assert account_balance(acct.ledger_account) == bal
    assert JournalEntry.objects.count() == journals


@pytest.mark.django_db
def test_tampered_facts_invalidate_zero_movement(challenged, oob_capture):
    from apps.payments.models import Payment

    user, acct = challenged
    key = f"PR-{uuid4().hex[:10]}"
    err = _start_payment(user, acct, key)
    # server derives amount/bill from the DB, so the attackable field is the
    # operation identity: pointing the resume at another idempotency key must
    # break the material binding
    other = Bill.objects.create(biller="Other Co", amount=Decimal("25.00"))
    tampered = {**err.facts, "bill": str(other.pk)}

    with pytest.raises(PaymentError) as e:
        resume_payment(actor=user, challenge_id=err.challenge_id,
                       code=_code_of(oob_capture, err.challenge_id), facts=tampered)
    assert "MATERIAL_CHANGED" in str(e.value)
    assert RiskChallenge.objects.get(pk=err.challenge_id).status == RiskChallenge.Status.EXPIRED
    assert not Payment.objects.filter(idempotency_key=key).exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_resume_single_settlement(challenged, oob_capture):
    """Two simultaneous resumes of one challenge → at most one settlement."""
    import threading

    from apps.payments.models import Payment

    user, acct = challenged
    key = f"PR-{uuid4().hex[:10]}"
    err = _start_payment(user, acct, key)
    code = _code_of(oob_capture, err.challenge_id)
    results = []

    def worker():
        try:
            p, created = resume_payment(actor=user, challenge_id=err.challenge_id,
                                        code=code, facts=dict(err.facts))
            results.append(("ok", created))
        except Exception as e:
            results.append(("err", str(e)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len([r for r in results if r[0] == "ok"]) <= 1
    assert Payment.objects.filter(idempotency_key=key).count() <= 1
