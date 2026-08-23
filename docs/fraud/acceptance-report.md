# Bankio Fraud & Risk Engine — Final Acceptance Report (Task 41)

Date: 2026-08-23 · Suite: 405 tests green · Tags: `bankio-fraud-engine-v1`,
`bankio-fraud-shadow-v1`, `bankio-fraud-challenge-v1`, `bankio-fraud-enforcement-v1`

## Judge checklist

**Explainable?** Yes. Every decision persists a snapshot: signals
(RiskSignal rows), triggered rules with versions and scores, ruleset digest,
policy version, risk score/level/decision. ATO and insider correlations emit
plain-language explanations.

**Versioned?** Yes. Rules are immutable versioned rows (DRAFT→TESTING→
APPROVED→ACTIVE→RETIRED) with maker-checker; policies carry
`policy-v1`; failsafe matrix is `failsafe-v1`; evaluations store the exact
versions that produced them.

**Auditable?** Yes. RULE_CREATED/ACTIVATED/DISABLED, FRAUD_MODE_CHANGED,
RISK_EVALUATION_ERROR, TRANSFER_FAILED(RISK_BLOCKED), CASE_* events are all
audit events. Evaluations are append-only snapshots; case timelines are
append-only.

**Deterministic?** Yes. Same snapshot + same ruleset ⇒ same decision
(property-tested scorer, pure rule/policy functions). No ML in the loop;
the optional `risk_model.predict(context)` seam remains available and the
policy engine would still decide.

**Safe to evolve?** Yes. Rules can be simulated against history
(`simulate_rule`, `backtest_shadow`) before activation; enforcement gate
(block ≤5%, review ≤20%) guards rollout; mode can be dialed back at runtime
through the permission-gated, audited `fraud_mode`.

## Lifecycle

REQUEST → AUTH → AUTHZ → business validation → RISK CONTEXT → SIGNALS →
RULES → SCORE → POLICY → ALLOW / CHALLENGE / REVIEW / BLOCK.
Enforced on TRANSFER, CARD_PURCHASE, BILL_PAYMENT; observed on LOGIN,
PROFILE_UPDATE/PASSWORD_CHANGE, ACCOUNT_OPENING, manager ops.
Decision precedes irreversible posting; block/challenge evidence is written
outside the settlement transaction so it survives rollback.

## Invariants 1–10

All hold and are exercised by tests (see regression-task40.md).

## Honest limitations

- No ground-truth fraud labels: precision/recall are None; FP work uses
  intervention-rate and contested-case proxies.
- Geography/contact-change signals intentionally absent (D-F04) — no data
  sources exist; fabricating them was rejected.
- Step-up challenge exists as a complete server-side workflow
  (issue/verify/consume, material-bound); no customer-facing UI screen yet —
  CHALLENGE_ONLY therefore halts operations with STEP_UP_REQUIRED.
- Dev-data backtest validated tooling mechanics, not rule quality; a run on
  production-like traffic is required before tightening thresholds.

Verdict: **ACCEPTED** — engine v1 meets the specification's quality gates.
