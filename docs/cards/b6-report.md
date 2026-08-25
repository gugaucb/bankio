# FASE 8 B6 — feat/cards-invoice-ui — Judge Report

## DESIGN
- Fatura atual: ciclo aberto derivado (open_cycle_composition) com total,
  data de fechamento e transações do ciclo.
- Faturas anteriores: snapshot CreditStatement (nunca reconstruído por
  "transações do mês"), status PAID/UNPAID, due date, paginação 12.
- Detail /invoices/<id>/: composição derivada + flag de consistência
  (snapshot vs derivação atual, p.ex. ajustes pós-ciclo) sem reescrever
  histórico. Ownership user+card+statement → IDOR 404 indistinguível.
- Link "Invoices →" no card detail.

## FILES
- identity/app_views.py (+card_invoices_view, card_invoice_detail_view)
  · urls.py (+2 rotas) · templates/dashboard/card_invoices.html (novo) ·
  card_invoice_detail.html (novo) · card_detail.html (+link) ·
  tests/test_cards_invoices_ui.py (+5).

## TESTES
fatura atual mostra ciclo aberto · anteriores com PAID/UNPAID · IDOR duplo
(statement alheia / card próprio com statement alheia → 404; dono → 200) ·
fatura fechada imutável após compras novas (Later Shop ausente) ·
paginação 14→2 páginas.

## GATES
make verify: **755 passed** · check limpo · migrations OK.

JUDGE: [✔] fatura atual [✔] histórico paginado de snapshots [✔] IDOR
[✔] histórico nunca reescrito [✔] design existente preservado

JUDGE VERDICT: PASS
