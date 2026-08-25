# MEMORY — FASE 8 Cartões e Faturas Avançados

## Objetivo
Evoluir o domínio de cartões EXISTENTE do BankIO (não reimplementar).
Baseline: **714 passed** (main @ ed0e822), check/migrations limpos.

## Inventário NÃO reimplementar
- Card/CardRequest/CardTransaction/CreditStatement models; purchase() com
  risk gate fora da tx + idempotency + ledger + notificações FASE 6;
  set_card_control/freeze/unfreeze/report_lost; request/decide flow;
  pay_statement básico; /app/cards/ lista+requests; last4-only (nunca PAN).

## Decisões
1. available_limit de crédito = DERIVADO (credit_limit − usado); usado =
   soma aprovadas − statements pagos. Sem segunda coluna mutável.
2. Fatura DERIVADA das CardTransactions elegíveis (declined=False,
   journal não nulo, created_at no ciclo). CreditStatement = snapshot
   mínimo do fechamento (period_start/end, amount_due, paid, due_date).
3. Fechamento idempotente via unique(card, period_end) + get_or_create.
4. Comando `manage.py close_card_invoices` (sem Celery/scheduler novo).
5. Controles customer-facing POST-only CSRF + ownership + audit.
6. Estados reais respeitados: FROZEN↔ACTIVE; BLOCKED (lost) terminal;
   replacement = novo CardRequest após lost (CARD_REPLACEMENT_REQUESTED).
7. Notificações sempre via notify(); monetárias on_commit.
8. Juros/mínimo/parcelamento/cashback/chargeback/PAN: FORA DE ESCOPO (§5).

## Branches (ordem obrigatória)
| # | Branch | Estado | Testes | Veredito |
|---|---|---|---|---|
| 1 | feat/cards-advanced-dashboard | pendente | — | — |
| 2 | feat/cards-controls-lifecycle | pendente | — | — |
| 3 | feat/cards-limits-availability | pendente | — | — |
| 4 | feat/cards-transactions-history | pendente | — | — |
| 5 | feat/cards-billing-cycle | pendente | — | — |
| 6 | feat/cards-invoice-ui | pendente | — | — |
| 7 | feat/cards-invoice-payment-advanced | pendente | — | — |
| 8 | feat/cards-advanced-notifications | pendente | — | — |
| 9 | test/cards-advanced-regression | pendente | — | — |

Discovery completo: docs/cards/fase8-discovery.md
Bug pré-existente conhecido: decline rows de controle sofrem rollback
(docs/notifications/b5-report.md) — não agravar.
