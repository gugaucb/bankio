"""Score engine (spec PART 6).

Deterministic: sum of triggered rule points, clamped to [0, 100].
Bands are centralized here — never hard-coded at call sites.
"""
from .models import RiskEvaluation

SCORE_MIN = 0
SCORE_MAX = 100

BANDS = [
    (30, RiskEvaluation.RiskLevel.LOW),
    (60, RiskEvaluation.RiskLevel.MEDIUM),
    (80, RiskEvaluation.RiskLevel.HIGH),
]


def score(triggered_rules):
    total = sum(int(r["score"]) for r in triggered_rules)
    return max(SCORE_MIN, min(SCORE_MAX, total))


def risk_level(score_value):
    for upper, level in BANDS:
        if score_value < upper:
            return level
    return RiskEvaluation.RiskLevel.CRITICAL
