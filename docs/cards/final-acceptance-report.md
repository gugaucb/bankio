# FASE 8 — FINAL ACCEPTANCE REPORT

Baseline: 714 passed (main @ ed0e822) → Final: **774 passed**
(main @ merge test/cards-advanced-regression). `make verify` limpo
(check + makemigrations --check + pytest) em todos os 9 merges.
Nenhum teste deletado / skipped / xfailed.

## DISCOVERY
docs/cards/fase8-discovery.md: inventário existente preservado, gap
matrix ✅/🟡/❌, riscos arquiteturais. Nada reimplementado; todo o
código novo estende apps/cards e a UI do dashboard.

## CARD EXPERIENCE (B1)
/app/cards/{id}/ com visual mascarado (last4), limites, fatura corrente,
últimas transações. Ownership server-side → 404 indistinguível.

## LIFECYCLE & CONTROLS (B2)
freeze/unfreeze/toggle_online/toggle_international/report_lost via POST+CSRF.
**Bug real corrigido**: report_lost passava status=BLOCKED mas
set_card_control ignorava BLOCKED — cartão perdido continuava ACTIVE e
comprável. Corrigido + audit CARD_REPLACEMENT_REQUESTED quando há cartão
BLOCKED na conta.

## LIMITS (B3)
Limite usado/disponível DERIVADO (aprovadas − statements pagos), sem
segunda fonte de verdade. Prova de race de overspend com
select_for_update no cartão (compras concorrentes não excedem limite).

## TRANSACTIONS (B4)
Histórico com filtros status/datas/merchant (validação tolerante),
detalhe com triple-ownership filter e reuso do recibo da FASE 5.

## BILLING CYCLE (B5)
Ciclo mês-calendário; elegíveis = declined=False ∧ journal≠null ∧ data
no período; fechamento idempotente unique(card, period_end)+get_or_create;
due_date = fechamento+10d; comando determinístico
`close_card_invoices --reference` (sem Celery).

## INVOICE UI (B6)
Fatura corrente derivada on-demand; histórico paginado como snapshot;
detalhe mostra composição + flag de consistência derived==snapshot.

## INVOICE PAYMENT (B7)
POST-only + CSRF; double-submit inofensivo via idempotency_key;
insuficiência de fundos falha segura; SEM pagamento parcial automático,
SEM juros (fora de escopo); fatura nunca paga 2×.

## NOTIFICATIONS (B8)
Tudo via notify() da FASE 6; lifecycle/invoice em transaction.on_commit;
dedup por statement/recipiente; declines genéricos (internals de risco
não vazam — reprovado em teste).

## SECURITY / ADVERSARIAL (B9)
- IDOR: 7 rotas varridas → 404 para não-dono (incl. POSTs).
- XSS: merchant malicioso escapado em todas as renderizações.
- Sensitive data: regex \b(cvv|pan)\b negativo; senha nunca presente.
- Pagination abuse tolerado (page=9999/-3).
- Falha de notificação nunca afeta settlement nem controles.

## LEDGER INVARIANTS
Compra aprovada = exatamente 1 journal, sempre balanceado (Σdébito−Σcrédito=0);
replay de idempotency key = single posting; decline/risk BLOCK = zero
movimento e sem resíduo de idempotência; nenhum mecanismo de reversão —
histórico imutável (guards save/delete nos journals POSTED).

## AUDIT
Ações de controle, request/decide, fechamento e pagamento auditadas.

## PERFORMANCE
Regressão completa estável (~4min); paginação em todas as listas.

## PROCESS NOTES
- Recorrente durante a fase: inserir views acima do anchor
  `def cards_view` roubava seus decorators (5×) — sempre capturado pelo
  legado tests/test_app_pages.py antes do merge.
- Diagnósticos Pyright do host = falsos positivos (venv invisível ao host).

## VEREDITO FINAL
Todos os 9 branches PASS no juiz independente; §71 Definition of Done
atendido. **JUDGE VERDICT: PASS**
