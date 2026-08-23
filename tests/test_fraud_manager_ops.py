"""Manager operations run shadow risk observations (spec PART 29)."""
import pytest

from apps.audit.models import AuditLog
from apps.fraud.engine import evaluate_operation  # noqa: F401
from apps.fraud.models import RiskEvaluation, RiskRule
from tests.test_manager_portal import (  # reuse established fixtures
    branches,  # noqa: F401
    customer_of_rel,  # noqa: F401
    make_test_account, rel_mgr,
)


@pytest.fixture(autouse=True)
def clean(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    RiskRule.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    AuditLog.objects.filter(action__in=["RISK_EVALUATION_ERROR", "ACCOUNT_RESTRICTED"]).delete()
    yield


def test_restriction_creates_manager_evaluation(rel_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    from apps.managerops.services import request_restriction

    request_restriction(manager=rel_mgr, account_id=acc.pk,
                        restriction_type="TRANSFER_BLOCK", reason="suspected fraud")
    ev = RiskEvaluation.objects.filter(operation_type="MANAGER_RESTRICTION").latest("pk")
    assert ev.actor == rel_mgr
    assert ev.status == RiskEvaluation.Status.COMPLETED


def test_limit_change_creates_evaluation(rel_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    from decimal import Decimal

    from apps.managerops.services import request_limit_change

    request_limit_change(manager=rel_mgr, account_id=acc.pk,
                         new_limit=Decimal("500.00"), reason="vip")
    assert RiskEvaluation.objects.filter(operation_type="MANAGER_LIMIT_CHANGE").exists()


def test_engine_crash_does_not_break_restriction(rel_mgr, customer_of_rel, monkeypatch):
    from apps.fraud import engine
    from apps.managerops.services import request_restriction

    monkeypatch.setattr(engine, "evaluate_operation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    acc = make_test_account(customer_of_rel)
    r = request_restriction(manager=rel_mgr, account_id=acc.pk,
                            restriction_type="TRANSFER_BLOCK", reason="x")
    assert r.active
    assert AuditLog.objects.filter(action="RISK_EVALUATION_ERROR").exists()
