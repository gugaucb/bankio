# FASE 6 B4 — feat/notification-payments-integration — Judge Report

## DESIGN
- PAYMENT_COMPLETED via `transaction.on_commit` dentro de
  `_pay_bill_atomic` — só após o commit do journal; dedup
  `PAYMENT_COMPLETED:{idempotency_key}:{created_by}`.
- Body: valor + biller + journal reference (mascarável); sem internals.
- Falha de notificação nunca altera payment/ledger (provado).

## FILES
- apps/payments/services.py (+1 hook) ·
  tests/test_notification_payments.py (+5).

## TESTES
completed exatamente 1× com reference no body · replay de idempotency_key
→ sem nova notificação e saldo intacto · insufficient funds → zero
notificação · _create→boom: payment COMPLETED, saldo 960, NOTIFICATION_ERROR
auditado · snapshot ledger intacto após leitura/marcação.

## GATES
pytest **682 passed** · check limpo · migrations OK.

JUDGE: [✔] completed/failed corretos [✔] risk states nunca viram sucesso
[✔] idempotência [✔] on_commit [✔] regressão verde

JUDGE VERDICT: PASS
