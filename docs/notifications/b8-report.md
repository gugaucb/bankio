# FASE 6 B8 — test/notifications-regression — Judge Report

## DESIGN
Suíte adversarial de regressão da stack completa de notificações
(core + center UI + transfers/payments/cards + security + preferences).

## BUG REAL ENCONTRADO E CORRIGIDO NESTE BRANCH
`dedup_key` tinha unique GLOBAL — dois usuários com a mesma chave semântica
colidiam e o evento do segundo era engolido (retornado como "replay" do
primeiro). Corrigido: unique constraint por (recipient, dedup_key)
(migration 0004) + recuperação de corrida filtrando por recipient.
Os emissores reais já incluíam recipient_id na chave; agora o modelo
também garante no banco.

## COBERTURA ADVERSARIAL (+9 testes)
- dedup: colisão de chave entre usuários → cada um recebe seu evento;
- payload abuse: kind oversized/charset inválido rejeitado + auditado ×2;
- IDOR deep-link POST /read de outro usuário → 404, nunca marcado;
- GET nunca é destrutivo (mark-read via GET não marca);
- XSS armazenado escapado na Central (script/img-onerror);
- paginação abusiva (?page=9999/-1/abc) → sem crash;
- filtros maliciosos (state/category SQL-like) sanitizados;
- invariantes financeiras: compra aprovada notifica só pós-commit, snapshot
  da cadeia de journals intacto após leituras/marcações, saldo correto;
  compra negada → zero movimento em ledger + decline customer-safe.

## FILES
- apps/notifications/models.py (constraint per-recipient) · migrations/0004 ·
  services.py (recuperação escopada) · tests/test_notifications_regression.py (+9).

## GATES
make verify: **714 passed** · check limpo · migrations OK.

JUDGE: [✔] bug real corrigido com migração [✔] adversarial amplo [✔]
invariantes financeiras provadas [✔] make verify verde

JUDGE VERDICT: PASS
