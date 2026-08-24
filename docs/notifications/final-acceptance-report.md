# FASE 6 — Notificações Transacionais e Central de Alertas — FINAL ACCEPTANCE REPORT

Baseline inicial: **643 passed** · Final: **714 passed** (+71 testes novos).

## Checklist por branch (merge --no-ff em main, relatórios em docs/notifications/)
| # | Branch | Relatório | Veredito |
|---|---|---|---|
| 1 | feat/notification-core (model estendido, notify() à prova de falhas, dedup DB) | b1-report.md | PASS |
| 2 | feat/notification-center-ui (central com filtros/paginação/mark-read/mark-all/badge) | b2-report.md | PASS |
| 3 | feat/notification-transfer-integration (COMPLETED/RECEIVED/FAILED/UNDER_REVIEW/REVERSED) | b3-report.md | PASS |
| 4 | feat/notification-payments-integration (PAYMENT_COMPLETED pós-commit) | b4-report.md | PASS |
| 5 | feat/notification-cards-integration (APPROVED on_commit; DECLINED pós-abort, textos safe) | b5-report.md | PASS |
| 6 | feat/notification-security-events (NEW_DEVICE/MFA/PASSWORD/BLOCKED — pontos reais) | b6-report.md | PASS |
| 7 | feat/notification-preferences (opt-out por categoria, mandatory bypass, audit, UI) | b7-report.md | PASS |
| 8 | test/notifications-regression (adversarial + fix dedup per-recipient) | b8-report.md | PASS |

## Critérios de aceite
- [x] Notificação NUNCA crítica: notify() nunca propaga falha; NOTIFICATION_ERROR auditado (provado com _create→boom em transfers/payments/cards).
- [x] Eventos monetários só via transaction.on_commit após settlement (provado; B8 prova ainda que não há notificação sem commit e que leitura/marcação não altera o snapshot da cadeia de journals).
- [x] Dedup idempotente em banco, corrida tratada por IntegrityError; escopado POR RECIPIENT (bug global corrigido na migração 0004).
- [x] MANDATORY_NOTIFICATION_KINDS nunca suprimidos por preferência.
- [x] Privacidade: zero score/regras/thresholds/OTP/reason de admin nos textos customer-safe.
- [x] AuditLog ≠ Notification; FraudAlert ≠ Notification.
- [x] In-app only: sem Celery/SMTP/push/WebSocket/SSE.
- [x] UI: central com estado/categoria, paginação, mark read POST-only, mark-all, badge, preferências; IDOR→404 indistinguível; XSS escapado; GET nunca destrutivo.
- [x] Regressão completa verde via `make verify` (check + migrations --check + pytest 714 passed).
- [x] MEMORY_6.md atualizado com contagens REAIS por branch e bugs encontrados.

## Bugs reais documentados
1. B5: on_commit dentro de transação abortada não dispara (decline de cartão); row de decline de controle não persistir é bug pré-existente do domínio cards (fora de escopo, relatado).
2. B8: dedup_key unique global → colisão entre usuários; corrigido para per-recipient.

FASE 6 CONCLUÍDA — 8/8 branches merged com JUDGE VERDICT: PASS.
