# S5.B1 — feat/account-statement-core — Judge Report

## DESIGN
- `apps/accounts/statement.py`: projeção READ-ONLY sobre LedgerEntry POSTED
  (StatementService). Nenhum model novo — ledger continua única fonte de
  verdade; StatementLine é dataclass sem persistência.
- Direção IN/OUT derivada da linha do ledger da conta visualizada
  (CREDIT→IN, DEBIT→OUT em LIABILITY), não do tipo global da operação.
- balance_after via Window(Sum) PostgreSQL ordenada por
  (posted_at, journal_id, id) — determinística, sem O(n²).
- Filtro journal__currency=account.currency → sem cross-currency.
- Reverse lookup de origem (Transfer/Payment/CardTransaction) em lote
  por journal_id__in: 3 queries fixas por página, sem N+1 (provado por teste).
- counterparty "—" quando não derivável com segurança (limitação documentada).

## FILES
- apps/accounts/statement.py (novo) · docs/statement/discovery-report.md ·
  tests/test_statement_core.py (+13).

## TESTES
conta vazia · entrada · saída · ordenação/mesmo timestamp · saldo corrente e
final == account_balance · DRAFT excluído · FAILED/risk block zero movimento ·
reversão mantém ambas as linhas · cross-currency excluído · IDOR
get_owned_account · paginação estável (30→25+5) · replay idempotente sem
duplicar · query count constante (3/página). Regressão: **605 passed**.
check limpo. migrations OK.

## JUDGE
[✔] ledger fonte de verdade, zero tabela paralela · [✔] só POSTED · [✔]
reversões explícitas · [✔] saldo confere · [✔] ordenação determinística ·
[✔] IDOR bloqueado · [✔] idempotência preservada · [✔] N+1 controlado ·
[✔] regressão verde

JUDGE VERDICT: PASS
