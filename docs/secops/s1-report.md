# S1 — feat/secops-engine-health — Judge Report

## DESIGN
- Console Security Operations (`/secops/health/`) em `apps/fraud/security_ops.py`
  — read-only, reutiliza `observability.engine_metrics` e
  `auth_metrics.login_metrics` (sem segunda implementação de métricas).
- Modelo de acesso: FRAUD_ANALYST, SENIOR_FRAUD_ANALYST, FRAUD_MANAGER,
  ADMIN, AUDITOR + superuser (`_require_secops_user`). CUSTOMER → 403;
  anônimo → login. Segregação de funções mantida: esta etapa NÃO altera
  modo nem regras.
- Painel: total de avaliações 24h, erros do engine (24h), latência p95 vs
  budget (observability.BUDGET_P95_MS), distribuição decisão/status/mode,
  métricas LOGIN (intervention/challenge/block rates + nota de honestidade),
  10 últimos RISK_EVALUATION_ERROR.

## FILES
- `apps/fraud/security_ops.py` — novo (guard + engine_health).
- `apps/fraud/urls.py` — rota secops_health.
- `templates/fraud/secops_health.html` — novo (shell padrão).
- `tests/test_secops_console.py` — 8 testes.

## TESTES
ADMIN/AUDITOR/FRAUD_MANAGER acessam · superuser acessa · CUSTOMER 403 ·
anônimo redirect · painel renderiza métricas.
Regressão: **582 passed**. check limpo.

## JUDGE
[✔] console staff-only com roles explícitos · [✔] read-only nesta fase ·
[✔] reuso de observability/auth_metrics · [✔] sem vazamento a customer ·
[✔] regressão verde

JUDGE VERDICT: PASS — PROMOTE para mode-control
