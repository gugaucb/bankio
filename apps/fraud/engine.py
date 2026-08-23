"""Risk evaluation orchestrator + immutable decision snapshot (spec PART 24).

evaluate_operation() is the single entry point used by protected flows:
    collect signals -> evaluate rules -> score -> policy decision -> persist
Every evaluation persists the exact signal values, triggered rules
(rule_id+version), score and policy/ruleset versions that produced the
decision — historical decisions are never recomputed with newer rules.
"""
from django.utils import timezone

from . import policies, rules, scoring, signals
from .context import RiskContext
from .models import RiskEvaluation, RiskSignal


def current_engine_mode():
    from . import modes

    return modes.get_mode()


def evaluate_operation(ctx: RiskContext, **domain_objects) -> RiskEvaluation:
    """Run the deterministic pipeline and persist a complete snapshot."""
    mode = current_engine_mode()
    evaluation = RiskEvaluation.objects.create(
        **ctx.evaluation_fields(),
        engine_mode=mode,
        status=RiskEvaluation.Status.EVALUATING,
        decision=RiskEvaluation.Decision.PENDING,
    )

    try:
        signal_values = signals.collect(ctx, **domain_objects)
        triggered, ruleset_version = rules.evaluate_rules(ctx.operation_type, signal_values)
        score_value = scoring.score(triggered)
        level = scoring.risk_level(score_value)
        decision = policies.decide(ctx.operation_type, level)

        evaluation.risk_score = score_value
        evaluation.risk_level = level
        evaluation.decision = decision
        evaluation.policy_version = policies.POLICY_VERSION
        evaluation.ruleset_version = ruleset_version
        evaluation.triggered_rules = triggered
        evaluation.signal_values = signal_values
        evaluation.status = RiskEvaluation.Status.COMPLETED
        evaluation.completed_at = timezone.now()
        evaluation.save()
    except Exception:
        # engine failure is explicit (INVARIANT 9); fail-safe resolution
        # per operation happens at the enforcement layer (Task 31)
        evaluation.status = RiskEvaluation.Status.FAILED
        evaluation.decision = RiskEvaluation.Decision.DEFER
        evaluation.completed_at = timezone.now()
        evaluation.save()
        raise

    RiskSignal.objects.bulk_create([
        RiskSignal(evaluation=evaluation, signal_id=sid, value=value)
        for sid, value in signal_values.items()
    ])
    return evaluation
