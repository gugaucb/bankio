"""Task 34: the shadow backtest run command produces honest, gated reports."""
import json

import pytest
from django.core.management import call_command
from io import StringIO

from apps.fraud.models import RiskEvaluation


def _seed_evals(n=20):
    for i in range(n):
        RiskEvaluation.objects.create(
            operation_type="TRANSFER",
            engine_mode=RiskEvaluation.EngineMode.SHADOW,
            status=RiskEvaluation.Status.COMPLETED,
            decision=RiskEvaluation.Decision.ALLOW,
            signal_values={"TRANSACTION_AMOUNT": 100 + i},
        )


@pytest.mark.django_db
def test_backtest_command_runs_and_reports_empty_honestly():
    out = StringIO()
    call_command("backtest_shadow", stdout=out)
    text = out.getvalue()
    assert "evaluations=0" in text
    assert "labels_available=False" in text
    assert "gate=PASS" in text or "gate=FAIL" in text


@pytest.mark.django_db
def test_backtest_command_counts_snapshots_and_json_parses():
    _seed_evals()
    out = StringIO()
    call_command("backtest_shadow", "--json", stdout=out)
    data = json.loads(out.getvalue())
    assert data["backtest"]["total"] == 20
    assert data["backtest"]["decisions"]["ALLOW"] == 20
    assert "engine_metrics" in data and "enforcement_gate" in data
