# MEMORY — FASE 6 Notificações Transacionais

## Objetivo
Central de alertas customer-facing in-app sobre o model `Notification`
existente. Sem worker/fila/canais externos. Baseline: **643 passed**.

## Inventário NÃO reimplementar
- `Notification` model + migration 0001; badge no shell.html;
  context processor `unread_notifications` (settings.py:70);
  notificações SECURITY de challenge em fraud/challenge_delivery.py:45
  e challenge_guard.py:174; padrões: Paginator, card/divide-y, máscara
  ••••last4, IDOR-404-indistinguível, adversarial suite FASE 5.

## Decisões
1. Notification = comunicação customer-facing ≠ AuditLog/FraudAlert/ledger.
2. Canal único in-app nesta fase; sem Celery/SMTP/push/WebSocket.
3. Criação monetária SEMPRE via transaction.on_commit() (nunca dentro do
   atomic de settlement).
4. dedup_key única POR RECIPIENT (constraint recipient+dedup_key, NULL
   permitido p/ legado) = idempotência; corrida tratada com IntegrityError
   escopado ao destinatário, nunca if-not-exists simples.
5. Service único `notify()` à prova de falhas: erro → AuditLog
   NOTIFICATION_ERROR (source/kind/reference segura) e retorno None;
   jamais propaga ao settlement. Proibido except:pass silencioso.
6. kind+metadata JSON seguros; sem score/regras/hash/OTP; autoescape,
   nunca |safe.
7. Categorias obrigatórias (SECURITY core kinds) não suprimíveis por
   preferência futura — whitelist central MANDATORY_NOTIFICATION_KINDS.
8. Dedup keys: TRANSFER:{reference}:EVENT:{recipient} /
   PAYMENT:{idempotency_key}:EVENT:{recipient} /
   CARD:{journal_reference}:EVENT:{recipient}.

## Progresso
| Branch | Escopo | Estado | Testes (acumulado real) | Veredito |
|---|---|---|---|---|
| feat/notification-core | model+service+dedup | concluído | 658 | PASS |
| feat/notification-center-ui | central UI | concluído | 670 | PASS |
| feat/notification-transfer-integration | transfers | concluído | 677 | PASS |
| feat/notification-payments-integration | payments | concluído | 682 | PASS |
| feat/notification-cards-integration | cards | concluído | 691 | PASS |
| feat/notification-security-events | security | concluído | 697 | PASS |
| feat/notification-preferences | preferências | concluído | 705 | PASS |
| test/notifications-regression | adversarial + fix dedup per-recipient | concluído | 714 | PASS |

## Bugs reais encontrados durante a fase
- B5: on_commit dentro de transação que aborta nunca dispara — decline de
  cartão emitido pós-abort no wrapper (relatado em b5-report.md; a row de
  decline de controle não persistir é bug pré-existente do domínio cards).
- B6: armadilha "todo dispositivo é novo" evitada — NEW_DEVICE só em
  Device.created=True; trusted reconectando não notifica.
- B8: dedup_key unique GLOBAL engolia eventos de usuários distintos com a
  mesma chave — corrigido para unique por recipient (migration 0004).

## Limitações conhecidas
- Badge faz COUNT por request autenticado (aceito nesta escala).
- Entrega é síncrona in-app; lembretes agendados não existem.
- seed_demo continua criando direto (dados fictícios, fora do fluxo real).
