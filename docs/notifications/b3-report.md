# FASE 6 B3 — feat/notification-transfer-integration — Judge Report

## DESIGN
- Eventos: TRANSFER_COMPLETED (remetente) + TRANSFER_RECEIVED (destinatário
  interno) via `transaction.on_commit` em `_settle` — só após o commit do
  journal POSTED; guarda anti-stale (status != COMPLETED → não anuncia).
- TRANSFER_FAILED p/ risk BLOCK: texto genérico ("could not be processed"),
  zero score/regras/thresholds no body/metadata.
- TRANSFER_UNDER_REVIEW: estado pendente explícito, emitido na rota
  UNDER_REVIEW do gate.
- TRANSFER_REVERSED via on_commit; original COMPLETED permanece.
- Dedup por destinatário: `{EVENT}:{reference}:{recipient_id}`.
- notify() nunca propaga falha (provado com _create→boom).

## FILES
- apps/transfers/services.py (+hooks, 5 pontos de emissão) ·
  tests/test_notification_transfers.py (+7).

## TESTES
completed+received exatamente 1× cada, recipients corretos · replay
idempotente → zero novas · risk block → só FAILED sem vocabulário de
sucesso nem internals · review → pendente, sem COMPLETED · reversão →
[COMPLETED, REVERSED] ambos preservados · falha de notificação →
transferência COMPLETED e saldo intacto + NOTIFICATION_ERROR auditado ·
leitura/marcação não altera ledger snapshot.

## GATES
pytest **677 passed** · check limpo · migrations OK.

JUDGE: [✔] semântica correta [✔] dedup provado [✔] on_commit provado
[✔] settlement independente [✔] regressão verde

JUDGE VERDICT: PASS
