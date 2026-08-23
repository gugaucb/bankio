"""Transfer integration: engine observes every attempt; money path unchanged in shadow."""
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.fraud.engine import evaluate_operation  # noqa: F401 (import sanity)
from apps.fraud.models import RiskEvaluation, RiskRule
from apps.ledger import services as ledger
from apps.transfers import services as transfers


@pytest.fixture
def accounts(db, django_user_model):
    sender = django_user_model.objects.create_user("ti-sender", email="tis@t.io", password="x")
    receiver = django_user_model.objects.create_user("ti-receiver", email="tir@t.io", password="x")
    cash = ledger.get_or_create_account(f"TI-CASH-{uuid4().hex[:6]}", "Cash", type="ASSET")
    rev = ledger.get_or_create_account("TI-REV", "Revenue", "INCOME")
    from apps.accounts.models import Account

    src = dst = None
    for user, amount in ((sender, "5000.00"), (receiver, "100.00")):
        la = ledger.get_or_create_account(
            f"2001-TI-{user.username}", f"Deposit {user.username}", is_customer=True
        )
        acct = Account.objects.create(
            customer=user,
            account_number=f"{abs(hash(user.username)) % 10**15:015d}",
            ledger_account=la,
        )
        ledger.post_journal(
            f"TI-DEP-{uuid4().hex[:8]}", "deposit",
            [(cash, "DEBIT", Decimal(amount)), (la, "CREDIT", Decimal(amount))],
        )
        if user == sender:
            src = acct
        else:
            dst = acct
    return sender, receiver, src, dst


@pytest.fixture(autouse=True)
def clean(settings):
    settings.FRAUD_MODE = "SHADOW"
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield
    RiskEvaluation.objects.all().delete()


def test_transfer_creates_correlated_shadow_evaluation(accounts):
    sender, _, src, dst = accounts
    key = f"TI-{uuid4().hex[:10]}"
    t, created = transfers.execute_transfer(
        actor=sender, source_account_id=src.pk,
        amount=Decimal("100.00"), destination_account_id=dst.pk,
        idempotency_key=key,
    )
    assert t.status == "COMPLETED" and created
    ev = RiskEvaluation.objects.get(idempotency_key=key)
    assert ev.operation_type == "TRANSFER"
    assert ev.status == RiskEvaluation.Status.COMPLETED
    assert ev.engine_mode == RiskEvaluation.EngineMode.SHADOW


def test_critical_rule_in_shadow_does_not_block_transfer(accounts):
    """INV 4 inverse: shadow BLOCK recommendation must not stop settlement."""
    RiskRule.objects.create(
        rule_id="SHADOW_BLOCK", name="sb", score=100,
        lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True,
    )
    sender, _, src, dst = accounts
    key = f"TI-SH-{uuid4().hex[:10]}"
    t, _ = transfers.execute_transfer(
        actor=sender, source_account_id=src.pk,
        amount=Decimal("50.00"), destination_account_id=dst.pk,
        idempotency_key=key,
    )
    assert t.status == "COMPLETED"
    ev = RiskEvaluation.objects.get(idempotency_key=key)
    assert ev.decision == RiskEvaluation.Decision.BLOCK  # recommended...
    assert t.status == "COMPLETED"                      # ...but not enforced


def test_engine_crash_does_not_break_money_path(accounts, monkeypatch):
    from apps.fraud import engine

    def explode(*a, **kw):
        raise RuntimeError("engine down")

    monkeypatch.setattr(engine, "evaluate_operation", explode)
    sender, _, src, dst = accounts
    t, _ = transfers.execute_transfer(
        actor=sender, source_account_id=src.pk,
        amount=Decimal("10.00"), destination_account_id=dst.pk,
        idempotency_key=f"TI-X-{uuid4().hex[:10]}",
    )
    assert t.status == "COMPLETED"


def test_retry_does_not_duplicate_evaluations_per_attempt_is_one(accounts):
    sender, _, src, dst = accounts
    key = f"TI-R-{uuid4().hex[:10]}"
    transfers.execute_transfer(actor=sender, source_account_id=src.pk,
                               amount=Decimal("5.00"), destination_account_id=dst.pk,
                               idempotency_key=key)
    transfers.execute_transfer(actor=sender, source_account_id=src.pk,
                               amount=Decimal("5.00"), destination_account_id=dst.pk,
                               idempotency_key=key)  # replay: returns original
    assert RiskEvaluation.objects.filter(idempotency_key=key).count() == 1
