# S5.B6 — test/statement-receipts-regression — FINAL Judge Report

## ESCOPO
- Hardening: journals DRAFT agora 404 em detalhe/comprovante (estavam
  visíveis ao dono — corrigido antes dos testes).
- tests/test_statement_adversarial.py (+8): manipulação de página
  (negativa/gigante/não-numérica), XSS `<script>` em descrição escapado em
  statement/print/detail, DRAFT sem leak, POST contra views read-only sem
  mutação (chain_hash+saldo intactos), double-submit/replay não duplica
  linha no CSV, cross-currency nunca somado, reconciliação último
  balance_after == account_balance com transfers+reversal mistos,
  varredura de imutabilidade completa (JournalEntry/RiskEvaluation/saldo
  intactos após extrato+print+CSV).

## FASE 5 — FINAL ACCEPTANCE REPORT

ARCHITECTURE: [✔] ledger única fonte de verdade · [✔] zero tabela paralela
(StatementLine = dataclass read-model) · [✔] JournalEntry POSTED imutável
(hash chain intacta nos sweeps)

STATEMENT: [✔] entradas/saídas por linha do ledger · [✔] saldo confere c/
balance service (window function + teste de reconciliação) · [✔] ordenação
determinística (posted_at, journal_id, id) · [✔] paginação server-side 25 ·
[✔] filtros período/direção/tipo · [✔] busca description/reference · [✔]
multimoeda (filtro journal.currency; EUR nunca aparece/soma)

REVERSALS: [✔] original preservado e visível · [✔] estorno explícito ·
[✔] links bidirecionais origem↔estorno

RECEIPTS: [✔] só COMPLETED/REVERSED/POSTED/nao-declinado · [✔] identidade =
reference real · [✔] IDOR 404 indistinguível · [✔] máscara •••• last4

EXPORT: [✔] CSV via StatementService · [✔] filtros respeitados · [✔]
_csv_safe neutraliza =+-@ · [✔] streaming + cap 5000 · [✔] print HTML sem
motor PDF, read-only

SECURITY: [✔] account/transaction/receipt IDOR protegidos (testes) · [✔]
CSRF nos forms Django · [✔] XSS avaliado (autoescape provado)

PERFORMANCE: [✔] projeção 3 queries fixas/página (teste) · [✔] export <25
queries p/ 60 linhas (chunked) · [✔] volume 30-60 linhas testado

FINANCIAL INVARIANTS: [✔] balance == ledger sempre · [✔] idempotência ·
[✔] double-spend protegido (regressão transfers) · [✔] risk block zero
movimento · [✔] reconciliation verde

REGRESSION: **643 passed** (baseline 592 → +51). check limpo. migrations OK.
Nenhum teste removido/skipado; único teste de domínio alterado: nenhum.
Domain fix justificado: Transfer.journal agora persistido em _settle
(estava nulo no banco); regressão de transfers integralmente verde.

JUDGE VERDICT: PASS
