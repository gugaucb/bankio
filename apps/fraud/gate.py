"""Shared risk-gate helper for full enforcement (spec PART 39).

Maps a stored evaluation + engine mode onto an action:
  ALLOW / REVIEW returned; interventions raised as RiskGateIntervention
  with action STEP_UP_REQUIRED or RISK_BLOCKED. SHADOW never intervenes.
Engine failure is handled upstream (fail-open with audit).
"""
from . import modes


class RiskGateIntervention(Exception):
    def __init__(self, action, evaluation=None):
        super().__init__(action)
        self.action = action
        self.evaluation = evaluation


def enforce(evaluation):
    """Return "ALLOW" or "REVIEW"; raise RiskGateIntervention otherwise."""
    if evaluation is None:
        return "ALLOW"
    effective = modes.effective_decision(evaluation)
    if effective == "CHALLENGE":
        raise RiskGateIntervention("STEP_UP_REQUIRED", evaluation)
    enforcing = evaluation.engine_mode == "ENFORCEMENT"
    if evaluation.decision == "BLOCK" and enforcing:
        raise RiskGateIntervention("RISK_BLOCKED", evaluation)
    return "REVIEW" if (evaluation.decision == "REVIEW" and enforcing) else "ALLOW"
