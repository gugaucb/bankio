# FASE 8 — CARD DOMAIN DISCOVERY REPORT

## Card models (evidence)
- **Card** (apps/cards/models.py:25): account FK, type, status, **last4 only**
  (PAN nunca persistido — models.py:29), holder_name, expiry_month/year,
  credit_limit, tx_limit(2000 default), daily_limit(3000), online_enabled,
  international_enabled, contactless_enabled, atm_enabled. `masked_number`
  property. `clean_limits()` rejeita limites negativos.
- **CardRequest** (:65): customer/account/type, status PENDING/APPROVED/
  REJECTED/CANCELED, requested_limit, approved_limit, decision_reason,
  reviewed_by, decided_at.
- **CardTransaction** (:94): card, merchant, amount, currency(USD),
  international, online, **declined(bool)+decline_reason**, journal FK
  (null p/ declines de controle que roll back), created_at.
- **CreditStatement** (:110): card, period_start, period_end, amount_due,
  paid, paid_at; unique(card, period_end).

## Card types / states
- Types: DEBIT_CARD, CREDIT_CARD, VIRTUAL_CARD, TEMPORARY_VIRTUAL_CARD,
  SINGLE_USE_CARD.
- States: ACTIVE, FROZEN, BLOCKED, EXPIRED, REPLACED (choices). Transições
  reais via set_card_control: FROZEN↔ACTIVE; report_lost_or_stolen→BLOCKED
  (irreversível — set_card_control recusa mudanças em BLOCKED).
- is_expired derivado da validade.

## Issuance flow
request_card(customer, account_id, type, limit) → PENDING (cap
MAX_REQUESTED_LIMIT=20000) → decide_card_request(aprover MANAGER,
approve, approved_limit) cria Card ACTIVE com credit_limit. Audit:
CARD_REQUESTED / CARD_REQUEST_APPROVED / CARD_REQUEST_REJECTED.

## Sensitive data
Somente last4 + masked_number. Sem PAN/CVV/PIN/token. ✅ preservar.

## Controles existentes
- set_card_control(actor, card_id, **controls): allowed = online/intl/
  contactless/atm enabled + tx/daily limits + status FROZEN/ACTIVE;
  select_for_update, _assert_owner, clean_limits, audit **CARD_UPDATED**
  (action único genérico).
- freeze_card/unfreeze_card/report_lost_or_stolen wrappers.

## Purchase flow (services.purchase)
amount>0 → idempotency key "card-purchase:{key}" replay via
ledger.find_idempotent ANTES do gate → risk gate FORA da tx (INV 9,
CHALLENGE step-up com confirm/reissue; enforce BLOCK fora da tx) →
_purchase_atomic (@atomic, select_for_update no card): EXPIRED/FROZEN/
CARD_{status}/ONLINE_DISABLED/INTERNATIONAL_DISABLED/TX_LIMIT_EXCEEDED/
DAILY_LIMIT_EXCEEDED (agregação do dia)/ACCOUNT_NOT_ACTIVE/
INSUFFICIENT_FUNDS (débito) ou CREDIT_LIMIT_EXCEEDED (crédito: usado =
soma aprovadas − statements pagos). Ledger: DEBIT conta cliente /
CREDIT 3000-CARD-RECEIVABLE; record_idempotent; on_commit notificação
FASE 6 (APPROVED/DECLINED com textos customer-safe).

**Bug conhecido B5**: `_decline_row` dentro do atomic sofre rollback →
declines de controle não persistem como CardTransaction (documentado em
docs/notifications/b5-report.md).

## CreditStatement / fatura
- **NADA cria CreditStatement** (nenhum close/cycle/job). pay_statement
  paga statements unpaid existentes (select_for_update card+statements,
  idempotency key opcional, INSUFICIENTE_FUNDS seguro, audit
  CARD_STATEMENT_PAID). ❌ ciclo/fechamento/vencimento ausentes.
- payments.Bill category "INVOICE" é biller invoice — NÃO relacionada.

## UI atual
- Apenas `/app/cards/` (cards_view, app_views.py:240): lista de cards +
  requests + form de request. Template dashboard/cards.html.
- **Nenhuma rota de detail/controles/histórico/fatura para cartões.**
- Statement FASE 5 é por Account (ledger), reutilizável como padrão.

## Limites / disponibilidade
- used/available de crédito calculados inline na compra (services.py:206)
  e NÃO expostos à UI. Débito usa available_balance da Account (derivado
  do ledger). Fonte única: ledger + CardTransaction. ❌ exposição UI.

## Reversal/refund
❌ Não existe estorno de compra de cartão.

## Notificações (FASE 6 na main ✓)
CARD_PURCHASE_APPROVED / CARD_PURCHASE_DECLINED via notify() central.
❌ CARD_FROZEN/UNFROZEN/MARKED_LOST/INVOICE_* ausentes.

## RBAC
is_bank_staff / role MANAGER decide requests; customer_only decorator nas
app views; ownership via _assert_owner e filtros account__customer=u.

## Testes atuais
test_cards.py (11), test_fraud_cards.py (4), test_step_up_card_resume.py
(5), test_notification_cards.py (9). Baseline total main: **714 passed**
(commit ed0e822), check/migrations limpos.

## GAP MATRIX (resumo)
| Requisito | Status | Evidência | Action |
|---|---|---|---|
| Lista de cartões customer | ✅ | cards_view | preservar/linkar detail |
| Card detail + controles UI | ❌ | sem rotas | Branch 1–2 |
| Freeze/unfreeze/online/intl UI | ❌ (serviço ✅) | services only | expor POST |
| Lost + replacement | 🟡 | lost=BLOQUEADO ok; replacement inexistente | audit + request |
| Limite total/disponível exibido | 🟡 | cálculo inline | extrair service |
| Histórico transações + filtros + detail | ❌ | sem rotas | Branch 4 |
| Billing cycle/fechamento/due date | ❌ | nada cria statement | Branch 5 |
| Fatura UI atual/anteriores | ❌ | — | Branch 6 |
| Pagamento fatura avançado (idem/notif/comprovante) | 🟡 | pay_statement básico | Branch 7 |
| Notificações lifecycle/invoice | ❌ | — | Branch 8 (FASE 6 presente) |
| Juros/mínimo/parcelamento/cashback/chargeback/PAN real | ❌ FORA DE ESCOPO | §5 | não inventar |

## Riscos de arquitetura
- Decline rows rollback (pré-existente) — não agravar.
- CREDIT_LIMIT check fora de lock por transações? Está DENTRO de
  _purchase_atomic com select_for_update no card → concorrência OK.
- Fatura deve ser DERIVADA das CardTransactions elegíveis (journal not
  null, declined=False) — evitar segunda fonte de verdade; snapshot no
  fechamento apenas com period/total (estado mínimo).
