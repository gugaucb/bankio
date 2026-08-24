# S5.B2 — feat/account-statement-ui — Judge Report

## DESIGN
- `/app/accounts/<id>/statement/`: extrato por conta, read-only, 25/página
  server-side. Consome exclusivamente o StatementService da B1 (mesma
  queryset/projeção — nenhuma query paralela).
- Header: tipo, número MASCARADO (•••• + last4), moeda, saldo atual
  (derivado do ledger via account.current_balance), status.
- Linhas: direção colorida IN/OUT, descrição, timestamp, counterparty,
  referência do journal, balance_after por linha.
- Empty state ("No movements"), IDOR→404 sem vazamento, anônimo→login,
  staff→redirect customer_only. Responsivo via dashboard/shell.html +
  Tailwind (sem JS framework). Link "View statement" na lista de contas.

## FILES
- apps/identity/app_views.py — account_statement_view.
- apps/identity/urls.py — rota app_account_statement.
- templates/dashboard/statement.html (novo) · accounts.html (link).
- tests/test_statement_ui.py (+7).

## TESTES
owner vê extrato c/ máscara+saldo · empty state · IDOR conta alheia → 404
sem vazar dados · anônimo 302 login · staff redirect · paginação server-side
(31 movimentos → 2 páginas, clamp de página inválida seguro).
Regressão: **612 passed**. check limpo. migrations OK.

## JUDGE
[✔] mesma fonte (StatementService/ledger) · [✔] paginação server-side ·
[✔] dados sensíveis mascarados · [✔] estados de UI cobertos · [✔] IDOR
bloqueado no servidor · [✔] regressão verde

JUDGE VERDICT: PASS
