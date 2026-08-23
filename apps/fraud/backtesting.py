"""Historical replay / backtesting (spec PART 16).

Replays completed evaluations' stored signal snapshots through a candidate
ruleset WITHOUT changing any historical or financial state. Where fraud
labels do not exist, precision/recall are explicitly reported as
unavailable — never fabricated (§55).
"""
from . import policies, scoring
from .rules import _condition_met

DECISION_ORDER = ("ALLOW", "CHALLENGE", "REVIEW", "BLOCK")


def _eligible(ruleset, operation_type):
    """Candidates are evaluated regardless of lifecycle — replay is exactly
    how drafts earn promotion; only the enabled flag is respected."""
    out = []
    for r in ruleset:
        if not getattr(r, "enabled", True):
            continue
        if r.operation_types and operation_type not in r.operation_types:
            continue
        out.append(r)
    return out


def backtest(ruleset, evaluations=None, limit=100000):
    from .models import RiskEvaluation

    if evaluations is None:
        evaluations = RiskEvaluation.objects.filter(status=RiskEvaluation.Status.COMPLETED)
    result = {
        "total": 0,
        "decisions": {d: 0 for d in DECISION_ORDER},
        "top_rules": {},
        "labels_available": False,   # honest reporting: no ground truth yet
        "precision_recall": None,
        "note": "No historical fraud labels exist; distribution metrics only.",
    }
    rule_list = list(ruleset)
    for ev in evaluations[:limit]:
        result["total"] += 1
        values = dict(ev.signal_values or {})
        triggered = []
        for rule in _eligible(rule_list, ev.operation_type):
            conditions = rule.conditions if isinstance(rule.conditions, list) else [rule.conditions]
            conditions = [c for c in conditions if c]
            if all(_condition_met(c, values) for c in conditions):
                triggered.append({"rule_id": rule.rule_id, "version": rule.version, "score": rule.score})
                key = f"{rule.rule_id} v{rule.version}"
                result["top_rules"][key] = result["top_rules"].get(key, 0) + 1
        level = scoring.risk_level(scoring.score(triggered))
        decision = policies.decide(ev.operation_type, level)
        result["decisions"][decision] += 1
    return result


def enforcement_gate(backtest_result, max_block_rate=0.05, max_review_rate=0.20):
    """Sanity gate before a ruleset may enter enforcement: the candidate must
    not block/review an implausible share of traffic."""
    total = max(backtest_result["total"], 1)
    block_rate = backtest_result["decisions"]["BLOCK"] / total
    review_rate = backtest_result["decisions"]["REVIEW"] / total
    ok = block_rate <= max_block_rate and review_rate <= max_review_rate
    return {
        "pass": ok,
        "block_rate": round(block_rate, 4),
        "review_rate": round(review_rate, 4),
        "thresholds": {"max_block_rate": max_block_rate, "max_review_rate": max_review_rate},
    }
