"""False-positive measurement under enforcement (spec PART 38).

Honesty first: without ground-truth fraud labels, "false positive"
cannot be computed directly. What we CAN measure from stored data:

  - intervention rate: share of enforced evaluations that blocked or
    challenged (the population from which FPs would come)
  - contested interventions: interventions whose customer later has an
    open fraud case disputing them — a lower bound on real FPs

Precision/recall remain None until labels exist.
"""
from django.db.models import Count
from django.utils import timezone

from .models import RiskEvaluation


def false_positive_report(window_hours=24 * 7):
    since = timezone.now() - timezone.timedelta(hours=window_hours)
    qs = RiskEvaluation.objects.filter(
        created_at__gte=since,
        engine_mode=RiskEvaluation.EngineMode.ENFORCEMENT,
        status=RiskEvaluation.Status.COMPLETED,
    )
    total = qs.count()
    by_decision = dict(qs.values_list("decision").annotate(n=Count("id")))
    intervened = by_decision.get(RiskEvaluation.Decision.BLOCK, 0) + by_decision.get(
        RiskEvaluation.Decision.CHALLENGE, 0)

    # lower-bound FP signal: open cases created after an intervention
    from .models import FraudAlert, FraudCase

    case_ids = FraudCase.objects.filter(opened_at__gte=since).values_list(
        "alerts__dedup_key", flat=True)
    contested = FraudAlert.objects.filter(dedup_key__in=[k for k in case_ids if k]).count()

    return {
        "window_hours": window_hours,
        "enforced_evaluations": total,
        "by_decision": by_decision,
        "interventions": intervened,
        "intervention_rate": round(intervened / total, 4) if total else None,
        "contested_alerts": contested,
        "labels_available": False,
        "precision_recall": None,
        "note": ("No ground-truth fraud labels; intervention rate and contested "
                 "cases are proxies only."),
    }
