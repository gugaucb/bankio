# S2 — feat/secops-mode-control — Judge Report

## DESIGN
- `/secops/mode/`: todos os SECOPS_ROLES veem modo atual + histórico
  (AuditLog FRAUD_MODE_CHANGED, últimos 20, from→to com ator).
- Troca de modo: POST gated por `has_permission("change_fraud_mode")` ou
  superuser — ADMIN/AUDITOR recebem 403 mesmo forjando POST (segregação
  §51 preservada; reusa modes.set_mode existente que audita e valida RBAC).
- Modo desconhecido → mensagem "Unknown mode", nada persistido.
- Link "Mode control" adicionado ao painel de health.

## FILES
- `apps/fraud/security_ops.py` — mode_control.
- `apps/fraud/urls.py` — rota secops_mode.
- `templates/fraud/secops_mode.html` — novo.
- `templates/fraud/secops_health.html` — link.
- `tests/test_secops_console.py` — +5 testes (13 total).

## TESTES
auditor vê read-only · auditor POST forjado → 403 sem persistir ·
customer POST → 403 · FRAUD_MANAGER troca com auditoria (metadata.to) ·
modo inválido rejeitado com mensagem. Regressão: **587 passed**. check limpo.

## JUDGE
[✔] visualização ampla p/ staff de security · [✔] mutação restrita a
change_fraud_mode/superuser · [✔] reuso total de set_mode/audit ·
[✔] histórico auditável na UI · [✔] regressão verde

JUDGE VERDICT: PASS — PROMOTE para evaluation-browser
