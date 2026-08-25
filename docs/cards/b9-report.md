# FASE 8 B9 — test/cards-advanced-regression — Judge Report

## DESIGN
- Final adversarial regression sweep over the whole cards stack (B1–B8),
  no production code touched — tests only.
- Financial invariants re-proven end-to-end: approved purchase posts
  EXACTLY ONE balanced double-entry journal even under idempotency-key
  replay; declines / risk blocks / invalid amounts leave the ledger and
  balance untouched with no idempotency residue; absence of any reversal
  mechanism keeps settled history immutable.
- Security regression: all 7 card routes (detail, transactions list,
  transaction detail, invoices list, invoice detail, POST controls,
  POST pay) return indistinguishable 404 for a non-owner; XSS merchant
  payloads escaped on every render; no PAN/CVV/PIN or password material
  anywhere in responses; pagination abuse (?page=9999/-3) tolerated;
  notification failure cannot affect settlement.

## FILES
- tests/test_cards_advanced_regression.py (+8).

## TESTES
approved purchase → single journal + balanced entries + replay dedup ·
decline/invalid → zero movement + find_idempotent None · history
immutability (no reversal) · IDOR sweep 7 routes → 404 · XSS escape ·
sensitive-data regex · pagination abuse · notification boom ≠ broken
settlement.

## GATES
make verify: **774 passed** (766 baseline + 8 new) · check limpo ·
migrations OK.

JUDGE: [✔] invariants financeiros [✔] IDOR/404 [✔] XSS/sensitive data
[✔] robustez [✔] notificação não-crítica

JUDGE VERDICT: PASS
