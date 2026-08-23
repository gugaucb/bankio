# MEMORY — Fase 2: Bankio Fraud & Risk Engine

Base: `main` @ 41dee3b (tag `bankio-ledger-anchored-v1`, 217 testes verdes).
Protocolo: DISCOVER → CRITÉRIOS → BRANCH `feat/fraud-XX-*` → IMPLEMENT → TEST → ATTACK → BACKTEST (quando aplicável) → JUDGE → merge `--no-ff` → verificação pós-merge → deletar branch.

## Board (41 tasks)

| # | Task | Branch | Status | Judge | Merged |
|---|------|--------|--------|-------|--------|
| 01 | Discovery: mapa de superfície de risco | (direto no main) | DONE | PASS | SIM (1524c27) |
| 02 | Modelos de domínio fraud | feat/fraud-02-domain-model | DONE | PASS | SIM (merge ad6281f) |
| 03 | RiskContext | feat/fraud-03-context | DONE | PASS | SIM (8767e98) |
| 04 | Signal registry | feat/fraud-04-signals | DONE | PASS | SIM (a052db6) |
| 05 | Velocity signals | feat/fraud-05-velocity | DONE | PASS | SIM (cf91cdd) |
| 06 | Rule engine | feat/fraud-06-rule-engine | DONE | PASS | SIM (7f408e8) |
| 07 | Rule versioning | feat/fraud-07-rule-versioning | DONE | PASS | SIM (f83cb8f) |
| 08 | Score engine | feat/fraud-08-score-engine | DONE | PASS | SIM (12e329f) |
| 09 | Policy engine | feat/fraud-09-policy-engine | DONE | PASS | SIM |
| 10 | Decision snapshot | feat/fraud-10-snapshot | pending | | |
| 11 | Shadow mode + engine modes | feat/fraud-11-shadow-mode | pending | | |
| 12 | Transfer integration (shadow) | feat/fraud-12-transfer | pending | | |
| 13 | Step-up auth (challenge/MFA) | feat/fraud-13-step-up | pending | | |
| 14 | Fraud alerts | feat/fraud-14-alerts | pending | | |
| 15 | Fraud cases | feat/fraud-15-cases | pending | | |
| 16 | Fraud RBAC | feat/fraud-16-rbac | pending | | |
| 17 | Analyst portal | feat/fraud-17-console | pending | | |
| 18 | Rule lifecycle management | feat/fraud-18-rule-mgmt | pending | | |
| 19 | Backtesting/replay | feat/fraud-19-backtesting | pending | | |
| 20 | Behavior baselines | feat/fraud-20-baselines | pending | | |
| 21 | Device signals | feat/fraud-21-device | pending | | |
| 22 | Beneficiary signals | feat/fraud-22-beneficiary | pending | | |
| 23 | Auth risk | feat/fraud-23-auth-risk | pending | | |
| 24 | ATO correlation | feat/fraud-24-ato | pending | | |
| 25 | Card integration | feat/fraud-25-cards | pending | | |
| 26 | Payments integration | feat/fraud-26-payments | pending | | |
| 27 | Sensitive profile changes | feat/fraud-27-profile | pending | | |
| 28 | Account opening | feat/fraud-28-opening | pending | | |
| 29 | Manager operations | feat/fraud-29-manager | pending | | |
| 30 | Insider risk | feat/fraud-30-insider | pending | | |
| 31 | Fail-safe policies | feat/fraud-31-failsafe | pending | | |
| 32 | Observability | feat/fraud-32-observability | pending | | |
| 33 | Adversarial suite | test/fraud-33-adversarial | pending | | |
| 34 | Shadow backtest run | test/fraud-34-shadow-backtest | pending | | |
| 35 | Challenge-only mode | feat/fraud-35-challenge | pending | | |
| 36 | Medição challenge | test/fraud-36-measure | pending | | |
| 37 | Limited enforcement | feat/fraud-37-limited | pending | | |
| 38 | Medição false positives | test/fraud-38-fp-measure | pending | | |
| 39 | Full enforcement | feat/fraud-39-enforcement | pending | | |
| 40 | Regressão completa Bankio | test/fraud-40-regression | pending | | |
| 41 | Judge final | — | pending | | |

## Tags alvo
- `bankio-fraud-engine-v1` — engine determinística aceita
- `bankio-fraud-shadow-v1` — shadow + backtest
- `bankio-fraud-challenge-v1` — challenge mode
- `bankio-fraud-enforcement-v1` — enforcement total

## Invariantes críticos (resumo)
1. Cliente não define o próprio risco. 2. Decisões server-side. 3. Decisões históricas guardam policy/ruleset version. 4. BLOCK pré-settlement = zero movimento no ledger. 5. Challenge não reutilizável p/ operação materialmente diferente. 6. Risco ≠ fraude confirmada. 7. Mudança de regra auditável. 8. Fraude não sobrepõe invariantes do ledger. 9. Falha do engine tem comportamento explícito (matriz fail-open/closed por operação). 10. Analista não manipula saldo.

## Decision Log
- D-F01: Fase usa arquivo separado `MEMORY_FRAUD.md` (MEMORY.md permanece como registro da Fase 1 ledger).
- D-F02: mapa de superfície em docs/fraud/risk-surface-map.md (versionado).
- D-F03: apps/fraud novo domínio; evaluate_fraud do compliance será desativado após integração de transfers.
- D-F04: signals de mudança de contato marcados N/A (endpoints inexistentes).

## Sessões
### Task 02 — Domain model
- apps/fraud: RiskEvaluation (decision/risk_level/mode/versioned), RiskSignal (fato único por evaluation), RiskRule (rule_id+version únicos, lifecycle DRAFT..RETIRED), FraudAlert (dedup_key), FraudCase (close exige decision_reason — check constraint).
- Lição: IntegrityError só é wrapped dentro de transaction.atomic() (psycopg error cru fora dele).
- Regressão: 223 green.
### Sessão 1 — Task 01 Discovery
- Git pre-flight: main limpa @41dee3b.
- Mapa de risco salvo em docs/fraud/risk-surface-map.md.
- Achado: apps/compliance já tem evaluate_fraud (só transfers, NEW_DEVICE_HIGH_VALUE é stub) → D-F03: novo apps/fraud absorve e substitui na Task 12.
- Gaps: cards/payments/loans/login sem risco; sem audit em loans/payments; sem labels históricos (backtest = distribuição, não precision/recall).
- D-F04: mudança de email/telefone não existe no app → signals PHONE/EMAIL_CHANGED ficarão N/A até endpoints existirem (documentado, não fabricado).
- D-F05: EXCEÇÃO ao protocolo — commit 1524c27 foi direto ao main por erro de fluxo (branch não criada); conteúdo era só documentação. A partir da Task 02 branches obrigatórias.
