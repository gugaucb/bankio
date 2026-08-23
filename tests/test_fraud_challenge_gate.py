"""Task 35: challenge-only enablement gate — mode switching + decision mapping."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from decimal import Decimal
from uuid import uuid4

from apps.fraud import modes
from apps.fraud.modes import effective_decision
from apps.fraud.models import FraudEngineSetting, RiskEvaluation, RiskRule


@pytest.fixture(autouse=True)
def clean(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    FraudEngineSetting.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield
    FraudEngineSetting.objects.all().delete()


@pytest.fixture
def manager(django_user_model):
    return django_user_model.objects.create_user(
        "chal-mgr", email="cm@t.io", password="x", role="FRAUD_MANAGER")


@pytest.mark.django_db
def test_mode_command_reads_and_sets(manager):
    out = StringIO()
    call_command("fraud_mode", stdout=out)
    assert "fraud_mode=SHADOW" in out.getvalue()

    call_command("fraud_mode", "CHALLENGE_ONLY", "--actor-id", str(manager.pk), stdout=out)
    assert "fraud_mode=CHALLENGE_ONLY" in out.getvalue()
    assert modes.get_mode() == "CHALLENGE_ONLY"


@pytest.mark.django_db
def test_mode_command_rejects_unauthorized_and_unknown(django_user_model):
    analyst = django_user_model.objects.create_user(
        "chal-analyst", email="ca@t.io", password="x", role="FRAUD_ANALYST")
    with pytest.raises(CommandError):
        call_command("fraud_mode", "ENFORCEMENT", "--actor-id", str(analyst.pk), stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("fraud_mode", "CHAOS", "--actor-id", "1", stdout=StringIO())


@pytest.mark.django_db
def test_challenge_only_downgrades_block_and_review(manager):
    modes.set_mode(RiskEvaluation.EngineMode.CHALLENGE_ONLY, actor=manager)
    for raw, expected in [
        (RiskEvaluation.Decision.BLOCK, "CHALLENGE"),
        (RiskEvaluation.Decision.REVIEW, "CHALLENGE"),
        (RiskEvaluation.Decision.ALLOW, "ALLOW"),
        (RiskEvaluation.Decision.CHALLENGE, "CHALLENGE"),
    ]:
        ev = RiskEvaluation.objects.create(
            operation_type="TRANSFER", engine_mode=RiskEvaluation.EngineMode.CHALLENGE_ONLY,
            status=RiskEvaluation.Status.COMPLETED, decision=raw,
        )
        assert effective_decision(ev) == expected, raw


@pytest.fixture
def accounts(db, django_user_model):
    from uuid import uuid4 as _uuid

    from apps.accounts.models import Account
    from apps.ledger import services as ledger

    sender = django_user_model.objects.create_user("co-sender", email="cos@t.io", password="x")
    receiver = django_user_model.objects.create_user("co-receiver", email="cor@t.io", password="x")
    cash = ledger.get_or_create_account(f"CO-CASH-{_uuid().hex[:6]}", "Cash", type="ASSET")
    src = dst = None
    for user, amount in ((sender, "5000.00"), (receiver, "100.00")):
        la = ledger.get_or_create_account(
            f"2001-CO-{user.username}", f"Deposit {user.username}", is_customer=True)
        acct = Account.objects.create(customer=user, account_number=f"88{user.pk:010d}",
                                      ledger_account=la)
        ledger.post_journal(
            f"CO-DEP-{_uuid().hex[:8]}", "dep",
            [(cash, "DEBIT", Decimal(amount)), (la, "CREDIT", Decimal(amount))],
        )
        if user == sender:
            src = acct
        else:
            dst = acct
    return sender, receiver, src, dst


@pytest.mark.django_db
def test_transfer_under_challenge_only_requires_step_up(accounts):
    """Since the enforcement cutover (Task 37), CHALLENGE_ONLY stops the flow
    with STEP_UP_REQUIRED and a bound challenge — nothing settles."""
    from apps.transfers.services import TransferError

    sender, _, src, dst = accounts
    RiskRule.objects.create(rule_id="CO-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    mgr = sender.__class__.objects.filter(role="FRAUD_MANAGER").first()
    if mgr is None:
        from django.contrib.auth import get_user_model

        mgr = get_user_model().objects.create_user("co-mgr", email="co@t.io", password="x",
                                                   role="FRAUD_MANAGER")
    modes.set_mode(RiskEvaluation.EngineMode.CHALLENGE_ONLY, actor=mgr)

    from apps.transfers import services as transfers

    key = f"CO-{uuid4().hex[:10]}"
    with pytest.raises(TransferError) as e:
        transfers.execute_transfer(
            actor=sender, source_account_id=src.pk,
            amount=Decimal("10.00"), destination_account_id=dst.pk,
            idempotency_key=key,
        )
    assert e.value.code == "STEP_UP_REQUIRED"
    ev = RiskEvaluation.objects.filter(idempotency_key=key).latest("pk")
    assert ev.decision == RiskEvaluation.Decision.BLOCK          # policy verdict preserved
    assert effective_decision(ev) == "CHALLENGE"                 # mode downgrades action
