# FASE 8 B4 — feat/cards-transactions-history — Judge Report

## DESIGN
- Histórico /app/cards/<id>/transactions/: server-side filtros período
  (from/to date-parse seguro — inválido ignorado), status
  approved/declined, merchant icontains; paginação 25; dados reais apenas.
- Detail /app/cards/<id>/transactions/<tx_id>/: ownership TRIPLO
  (user+card+transaction no filtro) → IDOR impossível; mostra merchant,
  valor, data, status, tipo online/intl, journal reference e link para o
  comprovante FASE 5 existente (app_transaction_receipt).
- DECLINED ≠ financeiro: rows declined não têm journal → sem receipt;
  distinguidas visualmente.

## FILES
- identity/app_views.py (+card_transactions_view,
  card_transaction_detail_view) · urls.py (+2 rotas) ·
  templates/dashboard/card_transactions.html (novo) ·
  card_transaction_detail.html (novo) · card_detail.html (+View all) ·
  tests/test_cards_transactions_history.py (+5).

## TESTES
lista+links · filtros status/merchant/período e datas inválidas seguras ·
paginação 30→2 páginas · ownership triplo (tx alheia→404; card próprio com
tx alheia→404; dono→200) · declined sem receipt/não confundido com
financeiro.

## GATES
make verify: **742 passed** · check limpo · migrations OK.

JUDGE: [✔] só dados reais [✔] status distintos [✔] IDOR triplo [✔]
paginação/filtros server-side [✔] reuso do comprovante FASE 5

JUDGE VERDICT: PASS
