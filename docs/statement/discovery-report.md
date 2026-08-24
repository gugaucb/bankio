# FASE 5 — DISCOVERY REPORT

Ledger account model: `ledger.LedgerAccount` (code unique, type, currency, is_customer_account; customer accounts are LIABILITY)
Journal model: `ledger.JournalEntry` (reference unique, status DRAFT|POSTED, posted_at, reverses FK self → reversed_by; immutable once POSTED)
Journal line model: `ledger.LedgerEntry` (journal FK entries, account FK entries, side DEBIT/CREDIT, amount > 0; index (account, side))
How balance is calculated: `ledger.services.account_balance` = Sum(POSTED entries) credits−debits for LIABILITY accounts — never a stored column.
How source operation is linked to journal: Transfer.journal FK, Payment.journal FK, CardTransaction.journal FK (null until posted).
Transfer → ledger relationship: Transfer.journal; status REVERSED on reversal.
Payment → ledger relationship: Payment.journal; statuses COMPLETED/FAILED/REVERSED.
Card → ledger relationship: CardTransaction.journal (null when declined); CreditStatement exists for credit cards only.
Reversal mechanism: `ledger.services.reverse_journal` creates mirror journal + sets original.reverses; original stays visible.
Existing account history: `templates/dashboard/transactions.html` + `app_views.transactions_view` — mixed transfers+card list from operation tables (NOT ledger-derived), no pagination, no per-account view, capped [:100]/[:50].
Existing card statement: CreditStatement model (credit cards); debit has nothing per-account.
Existing receipt functionality: none (no receipt/detail templates found).
Existing filters: transactions.html dir=in/out chips (operation-table based).
Existing pagination: Paginator used directly in fraud secops browser (25/page pattern).
Existing exports: none.
Account ownership validation: `Account.customer == request.user`; IDOR tests exist in test_e2e_journeys (journey 4).
Current UI patterns: dashboard/shell.html, card divs, Tailwind, HTMX for transfer results.
Current test baseline: 592 passed.
Potential reusable services: ledger.services.account_balance (reconciliation target), Paginator pattern, audit record().
Potential gaps: everything statement-specific (core projection, UI, filters, detail/receipts, export) — ❌.

## Classificação

- StatementService leitura do ledger POSTED: ❌ NÃO IMPLEMENTADO
- Direção entrada/saída da perspectiva da conta: ❌ (derivar de LedgerEntry.side vs account type)
- balance_after por linha (window function): ❌
- Ordenação determinística (posted_at, journal_id): ❌
- Página de extrato por conta c/ paginação server-side: ❌
- Filtros período/direção/fonte + busca: 🟡 PARCIAL (só dir=in/out sobre tabela Transfer; refazer sobre o service)
- Detalhe da operação / comprovante: ❌ (usar objetos reais Transfer/Payment/CardTransaction)
- Exportação CSV/print: ❌

Justificativa para não criar models: LedgerEntry+JournalEntry+FKs journal já representam cada movimentação com vínculo à operação de origem; extrato é query/projeção read-only. Nenhum model novo é necessário na Branch 1.

Limitações documentadas (não inventar):
- counterparty: derivável apenas quando há destination_account/beneficiary/merchant real; caso contrário "—".
- operation_reference: JournalEntry.reference; operation_type resolvido via reverse lookup das FKs journal (Transfer/Payment/CardTransaction) por journal_id.
- Journals DRAFT sem origem conhecida ficam fora do extrato contábil (regra).

Files expected to change: apps/accounts/statement.py (novo), templates/dashboard/statement.html, apps/identity/app_views.py + urls, tests/test_statement_core.py. Architectural risks: N+1 em counterparty (mitigar com lookup em lote dos journals da página); cross-currency (service fixa moeda da conta e filtra journal__currency=account.currency).
