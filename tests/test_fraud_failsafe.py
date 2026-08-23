"""Fail-safe matrix: explicit fail-open/fail-closed per operation (INV 9/10)."""
import pytest

from apps.audit.models import AuditLog
from apps.fraud import failsafe
from apps.fraud.engine import evaluate_operation
from apps.fraud.models import RiskEvaluation
from apps.fraud.signals import collect
from apps.fraud.context import RiskContext


def test_known_money_ops_fail_open():
    for op in ("TRANSFER", "CARD_PURCHASE", "BILL_PAYMENT", "ACCOUNT_OPENING"):
        assert failsafe.resolve_failure(op) == failsafe.FAIL_OPEN


def test_login_fails_closed():
    assert failsafe.resolve_failure("LOGIN") == failsafe.FAIL_CLOSED


def test_unknown_operation_fails_closed():
    """Safe direction is the default — matrix must be explicit to open."""
    assert failsafe.resolve_failure("SOMETHING_NEW") == failsafe.FAIL_CLOSED


@pytest.mark.django_db
def test_engine_failure_persists_failed_snapshot_and_reraises(django_user_model):
    """INV 9: failure is never silent and never becomes ALLOW."""
    user = django_user_model.objects.create_user("fs-user", email="fs@t.io", password="x")
    ctx = RiskContext(operation_type="TRANSFER", actor=user, customer=user)
    from unittest.mock import patch

    from apps.fraud import engine as engine_mod

    with patch.object(engine_mod.signals, "collect", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            evaluate_operation(ctx)
    ev = RiskEvaluation.objects.latest("pk")
    assert ev.status == RiskEvaluation.Status.FAILED
    assert ev.decision == RiskEvaluation.Decision.DEFER


def test_record_failure_audits_strategy(db):
    failsafe.record_failure("LOGIN", RuntimeError("down"))
    row = AuditLog.objects.filter(action="RISK_EVALUATION_ERROR").latest("pk")
    assert row.metadata["strategy"] == failsafe.FAIL_CLOSED


def test_signal_error_is_isolated_not_fatal(django_user_model):
    """Signal-level failures yield __error__ facts, not engine failure."""
    user = django_user_model.objects.create_user("fs-sig", email="s@t.io", password="x")
    ctx = RiskContext(operation_type="TRANSFER", actor=user, customer=user)
    from apps.fraud import signals

    @signals.register("FS_BOOM")
    def _boom(ctx, **kw):
        raise RuntimeError("signal blew up")

    try:
        values = collect(ctx)
    finally:
        signals.REGISTRY.pop("FS_BOOM", None)
    assert values["FS_BOOM"].get("__error__")
    assert set(values) >= {"FS_BOOM", "TRANSACTION_AMOUNT"}  # isolation, not abort
