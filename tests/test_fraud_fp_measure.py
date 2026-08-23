"""Task 38: false-positive measurement — honest proxies over stored data."""
import pytest

from apps.fraud.false_positives import false_positive_report
from apps.fraud.models import FraudAlert, FraudCase, RiskEvaluation


def _eval(decision, mode=RiskEvaluation.EngineMode.ENFORCEMENT):
    return RiskEvaluation.objects.create(
        operation_type="TRANSFER", engine_mode=mode,
        status=RiskEvaluation.Status.COMPLETED, decision=decision,
    )


@pytest.mark.django_db
def test_intervention_rate_counts_only_enforced():
    _eval(RiskEvaluation.Decision.BLOCK)
    _eval(RiskEvaluation.Decision.CHALLENGE)
    _eval(RiskEvaluation.Decision.ALLOW)
    # shadow evaluations never count as interventions
    _eval(RiskEvaluation.Decision.BLOCK, mode=RiskEvaluation.EngineMode.SHADOW)
    m = false_positive_report(window_hours=24 * 7)
    assert m["enforced_evaluations"] == 3
    assert m["interventions"] == 2
    assert m["intervention_rate"] == round(2 / 3, 4)


@pytest.mark.django_db
def test_empty_window_is_none_not_zero(db):
    m = false_positive_report(window_hours=24 * 7)
    assert m["enforced_evaluations"] == 0
    assert m["intervention_rate"] is None


@pytest.mark.django_db
def test_honesty_fields_always_present(db):
    m = false_positive_report()
    assert m["labels_available"] is False
    assert m["precision_recall"] is None
    assert "proxies" in m["note"]
