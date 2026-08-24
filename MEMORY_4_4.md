# MEMORY_4_4 — FASE 4.4 Security Operations para Admin

## Objetivo
Console staff-only sobre o Risk Engine: saúde/métricas, controle de modo,
browser de avaliações. Reuso obrigatório (sem reimplementar engine, RBAC,
audit).

## Inventário existente (NÃO reimplementar)
- observability.engine_metrics (latência/decisões/erros, budget p95)
- auth_metrics.login_metrics / login_backtest (FASE 4.3)
- modes.get_mode/set_mode (audita FRAUD_MODE_CHANGED; RBAC manage_policies)
- rbac.PERMISSIONS + has_permission; fraud views _require_fraud_user
- AuditLog (RISK_EVALUATION_ERROR, FRAUD_MODE_CHANGED)

## Modelo de acesso
SECOPS_ROLES = FRAUD_ANALYST, SENIOR_FRAUD_ANALYST, FRAUD_MANAGER, ADMIN,
AUDITOR (+superuser). CUSTOMER 403, anônimo redirect.

## Progresso
| Branch | Escopo | Status | Testes | Juiz |
|---|---|---|---|---|
| feat/secops-engine-health | painel saúde read-only | concluído | 582 passed | PASS |
| feat/secops-mode-control | modo atual + troca RBAC + histórico | concluído | 587 passed | PASS |
| feat/secops-evaluation-browser | lista paginada + detail | pendente | — | — |

## Decisões
- Console em apps/fraud/security_ops.py + rotas fraud:secops_* (mesmo app,
  guard distinto que inclui ADMIN/AUDITOR — oversight sem poder de mudança).
- Troca de modo continua gated por has_permission("change_fraud_mode")/
  superuser; ADMIN/AUDITOR são read-only (segregação §51).
- Browser mostra identifiers only (signal_values já armazenados na linha;
  nada de secrets novos).

## Limitações conhecidas
- Sem auto-refresh/websockets; métricas sob demanda.
- Paginação server-side simples (25/página).
