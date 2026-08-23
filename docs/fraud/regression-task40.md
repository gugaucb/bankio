# Task 40 — Complete Bankio Regression (2026-08-23)

## Commands run

| Check | Result |
|---|---|
| `manage.py check` | no issues |
| `manage.py makemigrations --check --dry-run` | no changes detected (models/migrations in sync) |
| `pytest -q` | **405 passed**, 0 failed (~90s) |
| `manage.py backtest_shadow` | runs; honest report; gate PASS |
| `manage.py fraud_mode` | reads mode; dev DB reset to SHADOW fallback |

## Coverage map (fraud suite)

- domain models, context, signals (core/velocity/beneficiary/auth/card/device)
- rules + versioning, scoring (property tests), policies, snapshots
- shadow mode, modes + audited switching (permission-gated)
- step-up challenges (TTL, single-use, material binding), alerts+dedup,
  cases+timeline, RBAC/SoD, console, rule management lifecycle, backtesting,
  baselines, ATO correlation, insider risk
- integrations: transfers (full enforcement gate), cards, payments, profile
  changes, account opening, manager ops
- adversarial suite, fail-safe matrix, observability, challenge metrics,
  false-positive proxies, backtest command

## Invariants spot-checked in suite

1–10 all exercised: client-side decisions impossible (INV 1/2 — adversarial
suite), versioned rules/snapshots retained (3), blocked = zero ledger
movement (4), challenge bound to material facts (5), auditable governance (6),
risk ≠ confirmed fraud — only FraudCase confirms (7), ledger invariants
unaffected by fraud layer (8), explicit fail behavior via failsafe-v1 (9),
analysts cannot touch balances or policies (10).

Verdict: **PASS** — main is a coherent, fully green state.
