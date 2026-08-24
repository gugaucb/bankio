# FASE 6 B5 — feat/notification-cards-integration — Judge Report

## DESIGN
- CARD_PURCHASE_APPROVED via `transaction.on_commit` em `_purchase_atomic`
  após o commit do journal; dedup `CARD_APPROVED:{journal.reference}:{customer_id}`.
- CARD_PURCHASE_DECLINED: emitido no wrapper `purchase()` capturando
  `CardDeclined` APÓS o abort de `_purchase_atomic` — chamada direta
  (sem on_commit, pois a transação já foi abortada e o callback nunca
  dispararia). Dedup `CARD_DECLINED:{idem_key}:{customer_id}`.
- Textos customer-safe por motivo (`_CARD_DECLINE_MESSAGES`: FROZEN,
  EXPIRED, TX/DAILY limit, ONLINE/INTERNATIONAL disabled, funds, credit)
  + genérico para demais; zero internals de risco.
- Falha de notificação nunca altera purchase/ledger (audit NOTIFICATION_ERROR).

## BUG PRÉ-EXISTENTE DOCUMENTADO (não corrigido neste branch — escopo notificação)
Descoberto pelos testes: `_decline_row` dentro de `_purchase_atomic`
(@atomic) é revertido pelo rollback do `CardDeclined` — linhas de decline
de controle (FROZEN/EXPIRED/limits) NÃO persistem em produção real;
apenas os declines de risk-gate/step-up (fora da tx) persistem. Os testes
legados de cards passavam porque não assertavam a persistência da row de
decline de controle. Correção financeira/domínio fica fora do escopo B5.

## FILES
- apps/cards/services.py (+hooks approved/declined, mapa de mensagens) ·
  tests/test_notification_cards.py (+9).

## TESTES
approved exatamente 1× com journal ref no body · declined limit/frozen com
texto correto · online/international metadata__reason · replay idempotente
→ 1 notificação apenas · _create→boom: purchase liquidado, saldo 998,
NOTIFICATION_ERROR auditado · snapshot ledger intacto · risk decline
(ENFORCEMENT) genérico sem score/rule/risk_evaluation.

## GATES
pytest **691 passed** · check limpo · migrations OK.

JUDGE: [✔] on_commit p/ aprovado provado [✔] decline pós-abort sem on_commit
[✔] dedup [✔] textos customer-safe [✔] settlement independente [✔] regressão verde

JUDGE VERDICT: PASS
