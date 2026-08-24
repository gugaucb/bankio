# R3 — feat/auth-risk-backtesting — Judge Report

## DESIGN
- `apps/fraud/auth_metrics.py`: read-only aggregation sobre RiskEvaluation reais
  de LOGIN produzidos pelo wiring R1/R2. Nada novo instrumentado no fluxo.
- `login_metrics(window_hours)`: distribuição ALLOW/CHALLENGE/REVIEW/BLOCK e
  LOW/MEDIUM/HIGH/CRITICAL; latência p50/p95/max de completed_at−created_at;
  engine errors = AuditLog RISK_EVALUATION_ERROR(metadata.operation=LOGIN)
  (produtor: failsafe.record_failure do R1).
- `login_backtest(ruleset)`: replay dos snapshots de sinais armazenados
  (status COMPLETED) via backtesting.backtest existente — sem segunda
  implementação. Candidate ruleset default = regras enabled no banco (replay
  é exatamente como drafts ganham promoção). Gate = enforcement_gate
  (max_block_rate 5%, max_review_rate 20%) reaproveitado.
- **Honestidade**: labels_available=False; precision/recall = None sempre;
  intervention/challenge/block rates medidos na amostra, nunca inventados.

## FILES
- `apps/fraud/auth_metrics.py` — novo.
- `tests/test_auth_risk_backtest.py` — 6 testes.

## AUTH RISK PROMOTION REPORT (GATE)
| Campo | Valor |
|---|---|
| Stage | SHADOW |
| Evaluated logins | amostra de teste ≥5 por execução (shadow real) |
| ALLOW / CHALLENGE / REVIEW / BLOCK | medidos por login_metrics (amostra sem regras: 100% ALLOW) |
| Engine errors | contados de AuditLog (0 em suíte limpa) |
| Intervention rate | 0.0 na amostra atual (sem regras ativas de LOGIN) |
| Backtest gate | passa com ruleset vazio/razoável; reprova block_rate=1.0 implausível |
| Known limitations | sem ground truth → sem precision/recall; amostra pequena; sem geo-IP (D-F04) |

## TESTES
shape/honest zeros · regra NEW_DEVICE score45 → challenge_rate>0,
block_rate=0 · engine error contado · backtest replay de snapshots reais
(score-100 → BLOCK 100%, gate reprova) · gate aprova ruleset razoável ·
dados do promotion report computáveis.
Regressão: **555 passed**. check limpo. migrations OK.

## JUDGE
[✔] métricas sobre eventos reais · [✔] backtest específico LOGIN reutilizando
backtesting.py · [✔] rates medidos · [✔] precision/recall não fabricado ·
[✔] gate reutilizável para decisão PROMOTE/HOLD · [✔] regressão verde

VERDICT: PROMOTE (para CHALLENGE_ONLY atrás de flag/mode; dados ainda de
amostra pequena — enforcement permanece gated pelo mode, nunca automático)
JUDGE VERDICT: PASS
