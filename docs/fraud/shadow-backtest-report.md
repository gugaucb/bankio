# Shadow Backtest Report — Task 34 (2026-08-23)

Command: `python manage.py backtest_shadow`

## Result (dev database, pre-challenge gate)

- rules enabled: 0
- shadow evaluations stored: 0
- decision distribution: all 0
- labels available: **False** — no ground-truth fraud labels exist; distribution metrics only
- enforcement gate: PASS (trivially — no data)
- engine errors (24h): 0; latency: n/a

## Honest reading

The dev environment holds no production-like traffic yet, so this run
validates the *tooling and gate mechanics* (command, JSON report, gate
thresholds), not rule quality. Precision/recall remain None by design.

## Gate criteria going forward

Before challenge-only enablement (Task 35) a backtest over real traffic
volumes must show block_rate ≤ 5% and review_rate ≤ 20%. The command is
the repeatable instrument for that measurement.
