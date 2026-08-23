"""Task 37: limited enforcement — engine decisions now gate transfers.

Cutover (D-F03): legacy compliance.evaluate_fraud no longer participates
in execute_transfer; the fraud engine is the single decision surface.
"""
import pytest
from decimal import Decimal
from uuid import uuid4

from apps.audit.models import AuditLog
from apps.fraud import modes
from apps.fraud.models import FraudEngineSetting, RiskChallenge, RiskEvaluation, RiskRule
from apps.ledger.models import JournalEntry


@pytest.fixture(autouse=True)
def clean(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    FraudEngineSetting.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield
    FraudEngineSetting.objects.all().delete()


@pytest.fixture
def accounts(db, django_user_model):
    from apps.accounts.models import Account
    from apps.ledger import services as ledger

    sender = django_user_model.objects.create_user("le-sender", email="les@t.io", password="x")
    receiver = django_user_model.objects.create_user("le-receiver", email="ler@t.io", password="x")
    cash = ledger.get_or_create_account(f"LE-CASH-{uuid4().hex[:6]}", "Cash", type="ASSET")
    src = dst = None
    for user, amount in ((sender, "5000.00"), (receiver, "100.00")):
        la = ledger.get_or_create_account(
            f"2001-LE-{user.username}", f"Deposit {user.username}", is_customer=True)
        acct = Account.objects.create(customer=user, account_number=f"99{user.pk:010d}",
                                      ledger_account=la)
        ledger.post_journal(
            f"LE-DEP-{uuid4().hex[:8]}", "dep",
            [(cash, "DEBIT", Decimal(amount)), (la, "CREDIT", Decimal(amount))],
        )
        if user == sender:
            src = acct
        else:
            dst = acct
    return sender, receiver, src, dst


@pytest.fixture
def manager(django_user_model):
    return django_user_model.objects.create_user(
        "le-mgr", email="lem@t.io", password="x", role="FRAUD_MANAGER")


@pytest.mark.django_db
def test_shadow_mode_still_never_blocks(accounts):
    """Regression: cutover must not change SHADOW behavior."""
    sender, _, src, dst = accounts
    RiskRule.objects.create(rule_id="LE-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    from apps.transfers import services as transfers

    t, created = transfers.execute_transfer(
        actor=sender, source_account_id=src.pk, amount=Decimal("10.00"),
        destination_account_id=dst.pk, idempotency_key=f"LE-SH-{uuid4().hex[:8]}")
    assert t.status == "COMPLETED"


@pytest.mark.django_db
def test_enforcement_block_stops_transfer_with_zero_ledger_movement(accounts, manager):
    modes.set_mode(RiskEvaluation.EngineMode.ENFORCEMENT, actor=manager)
    sender, _, src, dst = accounts
    RiskRule.objects.create(rule_id="LE-BLOCK2", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    from apps.transfers.services import TransferError, execute_transfer

    key = f"LE-BL-{uuid4().hex[:8]}"
    with pytest.raises(TransferError) as e:
        execute_transfer(actor=sender, source_account_id=src.pk, amount=Decimal("10.00"),
                         destination_account_id=dst.pk, idempotency_key=key)
    assert e.value.code == "RISK_BLOCKED"
    # INV 4: blocked = zero ledger movement
    assert not JournalEntry.objects.filter(reference__contains=key).exists()
    t = _failed_transfer(key)
    assert t is not None and t.status == "FAILED"
    ev = RiskEvaluation.objects.get(idempotency_key=key)
    assert ev.decision == RiskEvaluation.Decision.BLOCK


def _failed_transfer(key):
    from apps.transfers.models import Transfer

    return Transfer.objects.filter(idempotency_key=key).first()


@pytest.mark.django_db
def test_challenge_only_raises_step_up_with_bound_challenge(accounts, manager):
    modes.set_mode(RiskEvaluation.EngineMode.CHALLENGE_ONLY, actor=manager)
    sender, _, src, dst = accounts
    RiskRule.objects.create(rule_id="LE-CHAL", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    from apps.transfers.services import TransferError, execute_transfer

    key = f"LE-CH-{uuid4().hex[:8]}"
    with pytest.raises(TransferError) as e:
        execute_transfer(actor=sender, source_account_id=src.pk, amount=Decimal("25.00"),
                         destination_account_id=dst.pk, idempotency_key=key)
    assert e.value.code == "STEP_UP_REQUIRED"
    ch = RiskChallenge.objects.latest("pk")
    assert ch.evaluation.idempotency_key == key
    # nothing settled
    assert not JournalEntry.objects.filter(reference__contains=key).exists()


@pytest.mark.django_db
def test_engine_failure_in_enforcement_is_fail_open_audited(accounts, manager, monkeypatch):
    modes.set_mode(RiskEvaluation.EngineMode.ENFORCEMENT, actor=manager)
    sender, _, src, dst = accounts
    from apps.fraud import engine
    from apps.transfers.services import execute_transfer

    monkeypatch.setattr(engine, "evaluate_operation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    key = f"LE-FO-{uuid4().hex[:8]}"
    t, created = execute_transfer(
        actor=sender, source_account_id=src.pk, amount=Decimal("5.00"),
        destination_account_id=dst.pk, idempotency_key=key)
    assert t.status == "COMPLETED"  # failsafe-v1: TRANSFER fail-open
    assert AuditLog.objects.filter(action="RISK_EVALUATION_ERROR").exists()


@pytest.mark.django_db
def test_enforcement_review_routes_to_under_review(accounts, manager):
    modes.set_mode(RiskEvaluation.EngineMode.ENFORCEMENT, actor=manager)
    sender, _, src, dst = accounts
    RiskRule.objects.create(rule_id="LE-REV", name="n", score=65,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    from apps.transfers.services import execute_transfer

    t, created = execute_transfer(
        actor=sender, source_account_id=src.pk, amount=Decimal("10.00"),
        destination_account_id=dst.pk, idempotency_key=f"LE-RV-{uuid4().hex[:8]}")
    assert t.status == "UNDER_REVIEW"
