# FASE 6 B1 — feat/notification-core — Judge Report

## DESIGN
- Model `Notification` EXISTENTE estendido (sem tabela paralela):
  `kind` (64), `metadata` JSON (minimizada), `dedup_key` unique nullable
  (idempotência no BANCO — NULL permitido p/ legado), `read_at`,
  category → choices SYSTEM/SECURITY/TRANSFER/PAYMENT/CARD,
  ordering determinístico (-created_at,-id), índice (recipient,read).
- Migration 0002 compatível com dados existentes.
- Service único `notify()` em apps/notifications/services.py:
  valida categoria/kind, sanitiza metadata (str≤140/bool/int; rejeita
  tipos aninhados e oversize), dedup via constraint + recuperação de
  IntegrityError (nunca if-not-exists), NUNCA propaga exceção — falha
  vira AuditLog NOTIFICATION_ERROR (source/kind/category/recipient_id/
  exception class) e retorna None. mark_read idempotente c/ read_at;
  mark_all_read em bulk. Whitelist central MANDATORY_NOTIFICATION_KINDS.
- challenge_delivery/challenge_guard migrados para notify() preservando
  texto/categoria/comportamento (dedup por challenge.pk — reemissão
  continua criando nova notificação intencionalmente).

## TESTES (+15)
create básico · dedup mesma chave · eventos diferentes · recipients
diferentes · constraint única no banco provada (IntegrityError) ·
serviço converte corrida em replay idempotente · categoria/kind
inválidos auditados sem raise · metadata oversize/tipo inválido ·
falha não propaga p/ caller financeiro (monkeypatch _create→boom) ·
payload rejeita objetos/nested, aceita strings limitadas · challenge
via issue_and_deliver mantém notificação SECURITY sem código no body ·
mark_read idempotente · mark_all_read · whitelist obrigatória ·
legado ordenável.

## GATES
pytest **658 passed** (baseline 643 +15) · check limpo · migrations OK ·
make verify PASS.

JUDGE: [✔] model reutilizado [✔] sem 2ª tabela [✔] migration compatível
[✔] service único [✔] dedup no banco [✔] concorrência testada [✔]
challenge intacto [✔] erro auditado não-silencioso [✔] falha não quebra
caller [✔] regressão verde

JUDGE VERDICT: PASS
