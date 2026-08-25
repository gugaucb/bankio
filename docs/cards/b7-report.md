# FASE 8 B7 — feat/cards-invoice-payment-advanced — Judge Report

## DESIGN
- pay_statement EXISTENTE estendido, não recriado: notificação
  CARD_INVOICE_PAID via transaction.on_commit (só pós-commit), dedup
  CARD_INVOICE_PAID:{journal.reference}:{customer}.
- UI POST-only /app/cards/<id>/invoices/pay/ com ownership (404),
  idempotency_key determinística ui:{card}:{user}:{statement},
  mensagens de erro seguras (INSUFFICIENT_FUNDS etc.), redirect.
- Botão "Pay open invoices" na página de faturas.
- Pagamento parcial/juros/mínimo: NÃO implementados (§46/§47 — escopo
  total apenas; crédito pertence à FASE 9).

## FILES
- apps/cards/services.py (+on_commit notify em pay_statement) ·
  identity/app_views.py (+card_pay_invoice_view) · urls.py (+1 rota) ·
  card_invoices.html (+form) · tests/test_cards_invoice_payment.py (+6).

## TESTES
pagamento válido marca PAID + notifica $120.00 + saldo 380 · saldo
insuficiente: fatura UNPAID, saldo intacto, zero posting parcial ·
double submit cobra UMA vez (saldo 440) · fatura alheia/cartão inexistente
→ 404 sem pagar · GET nunca paga · _create→boom não quebra settlement.

## GATES
make verify: **761 passed** · check limpo · migrations OK.

JUDGE: [✔] reuso do serviço [✔] idempotência/concorrência [✔]
insuficiente seguro [✔] on_commit provado [✔] sem juros inventados

JUDGE VERDICT: PASS
