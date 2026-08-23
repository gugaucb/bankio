"""Engine observability: decision distribution, latency, error accounting."""
import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.fraud.models import RiskEvaluation
from apps.fraud.observability import BUDGET_P95_MS, engine_metrics


def _eval(**kw):
    defaults = dict(
        operation_type="TRANSFER", status=RiskEvaluation.Status.COMPLETED,
        decision=RiskEvaluation.Decision.ALLOW, engine_mode=RiskEvaluation.EngineMode.SHADOW,
    )
    defaults.update(kw)
    return RiskEvaluation.objects.create(**defaults)


@pytest.mark.django_db
def test_counts_decisions_statuses_and_errors():
    _eval()
    _eval(decision=RiskEvaluation.Decision.BLOCK)
    _eval(status=RiskEvaluation.Status.FAILED, decision=RiskEvaluation.Decision.DEFER,
          completed_at=timezone.now())
    AuditLog.objects.create(action="RISK_EVALUATION_ERROR", metadata={"op": "TRANSFER"})
    m = engine_metrics(window_hours=24)
    assert m["total_evaluations"] == 3
    assert m["by_decision"]["ALLOW"] == 1
    assert m["by_status"]["FAILED"] == 1
    assert m["engine_errors"] == 1


@pytest.mark.django_db
def test_latency_percentiles_computed():
    for ms in (10, 50, 300):
        ev = _eval()
        RiskEvaluation.objects.filter(pk=ev.pk).update(
            completed_at=ev.created_at + timezone.timedelta(milliseconds=ms))
    m = engine_metrics(window_hours=24)
    lat = m["latency_ms"]
    assert lat["samples"] == 3
    assert 0 < lat["p50"] <= lat["p95"] <= lat["max"]


@pytest.mark.django_db
def test_budget_flag_reflects_p95():
    ev = _eval()
    RiskEvaluation.objects.filter(pk=ev.pk).update(
        completed_at=ev.created_at + timezone.timedelta(milliseconds=BUDGET_P95_MS * 10))
    m = engine_metrics(window_hours=24)
    assert m["within_budget"] is False


@pytest.mark.django_db
def test_empty_window_is_honest(db):
    m = engine_metrics(window_hours=24)
    assert m["total_evaluations"] == 0
    assert m["latency_ms"] is None
    assert m["within_budget"] is False  # no data is not "passing"
