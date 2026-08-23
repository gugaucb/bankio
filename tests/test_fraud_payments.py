"""Payment fraud: shadow observation on bill settlement; crash-safe."""
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.accounts.models import Account
from apps.audit.models import AuditLog
from apps.fraud.engine import evaluate_operation  # noqa: F401
from apps.fraud.models import RiskEvaluation, RiskRule
from apps.ledger import services as ledger
from apps.payments.models import Bill
from apps.payments.services import pay_bill


@pytest.fixture(autouse=True)
def clean(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield


@pytest.fixture
def payer(db, django_user_model):
    user = django_user_model.objects.create_user("bill-user", email="bu@t.io", password="x")
    la = ledger.get_or_create_account("2001-BILLUSER", "bill deposit", is_customer=True)
    cash = ledger.get_or_create_account("BILL-CASH", "Cash", type="ASSET")
    ledger.post_journal(
        f"BILL-DEP-{uuid4().hex[:8]}", "dep",
        [(cash, "DEBIT", Decimal("5000.00")), (la, "CREDIT", Decimal("5000.00"))],
    )
    acct = Account.objects.create(customer=user, account_number="7777777777777772", ledger_account=la)
    bill = Bill.objects.create(biller="ACME Energy", amount=Decimal("120.50"))
    return user, acct, bill


def test_pay_bill_creates_shadow_evaluation(payer):
    user, acct, bill = payer
    payment, created = pay_bill(actor=user, account_id=acct.pk, bill_id=bill.pk, idempotency_key=uuid4().hex)
    assert created
    ev = RiskEvaluation.objects.filter(operation_type="BILL_PAYMENT").latest("pk")
    assert ev.status == RiskEvaluation.Status.COMPLETED
    assert ev.engine_mode == RiskEvaluation.EngineMode.SHADOW
    assert ev.idempotency_key == payment.idempotency_key


def test_engine_crash_does_not_break_bill_settlement(payer, monkeypatch):
    from apps.fraud import engine

    monkeypatch.setattr(engine, "evaluate_operation", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    user, acct, bill = payer
    key = uuid4().hex
    payment, created = pay_bill(actor=user, account_id=acct.pk, bill_id=bill.pk, idempotency_key=key)
    assert created
    assert AuditLog.objects.filter(action="RISK_EVALUATION_ERROR").exists()


def test_shadow_block_does_not_prevent_settlement(payer):
    """Shadow BLOCK is recorded but never stops the payment (spec PART 8)."""
    user, acct, bill = payer
    RiskRule.objects.create(
        rule_id="PAY-BLOCK-ALL", name="block all bills", score=100,
        lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True,
    )
    key = uuid4().hex
    payment, created = pay_bill(actor=user, account_id=acct.pk, bill_id=bill.pk, idempotency_key=key)
    assert created  # settlement happened despite BLOCK recommendation
    ev = RiskEvaluation.objects.filter(idempotency_key=key).latest("pk")
    assert ev.decision == RiskEvaluation.Decision.BLOCK
