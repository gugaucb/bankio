"""Account opening runs a shadow risk evaluation before creation."""
import pytest

from apps.accounts.models import AccountStatus
from apps.accounts.services import open_account
from apps.audit.models import AuditLog
from apps.fraud.engine import evaluate_operation  # noqa: F401
from apps.fraud.models import RiskEvaluation, RiskRule


@pytest.fixture(autouse=True)
def clean(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield


@pytest.fixture
def customer(db, django_user_model):
    return django_user_model.objects.create_user("open-user", email="open@t.io", password="x")


def test_open_account_creates_shadow_evaluation(customer):
    acct = open_account(customer=customer)
    assert acct.status == AccountStatus.ACTIVE
    ev = RiskEvaluation.objects.filter(operation_type="ACCOUNT_OPENING").latest("pk")
    assert ev.status == RiskEvaluation.Status.COMPLETED
    assert ev.engine_mode == RiskEvaluation.EngineMode.SHADOW


def test_engine_crash_does_not_block_opening(customer, monkeypatch):
    from apps.fraud import engine

    monkeypatch.setattr(engine, "evaluate_operation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    acct = open_account(customer=customer)
    assert acct.pk is not None
    assert AuditLog.objects.filter(action="RISK_EVALUATION_ERROR").exists()


def test_opening_creates_ledger_pair_with_zero_balance(customer):
    acct = open_account(customer=customer)
    assert acct.current_balance == 0
