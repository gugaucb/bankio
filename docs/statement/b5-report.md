# S5.B5 — feat/account-statement-export — Judge Report

## DESIGN
- CSV: `/app/accounts/<id>/statement/export.csv` — usa EXATAMENTE o
  StatementService + apply_filters (mesma queryset do extrato; zero query
  paralela). StreamingHttpResponse em chunks de 500 linhas, cap duro
  MAX_ROWS=5000. Colunas Date/Description/Type/In/Out/Balance/Reference;
  In/Out em colunas separadas (sem somar direções).
- CSV injection: _csv_safe prefixa ' para campos textuais iniciados por
  = + - @ tab CR — cobre descrição e reference; provado por teste com
  =HYPERLINK.
- Print: `/statement/print/` HTML print-friendly (window.print), mesmo
  service+filtros, cap 300 linhas, sem novo motor PDF.
- Auditoria: STATEMENT_EXPORTED com apenas last4 da conta e contagem de
  linhas (sem conteúdo financeiro no metadata).

## FILES
- apps/identity/app_views.py (+2 views, _csv_safe) · urls.py (+2 rotas) ·
  templates/dashboard/statement_print.html (novo) · statement.html
  (botões Print/Export CSV preservando filtros) ·
  tests/test_statement_export.py (+7).

## TESTES
cabeçalho+colunas In/Out corretas · CSV respeita filtro q (header-only) ·
IDOR export → 404 · =HYPERLINK neutralizado (célula inicia com '=
— csv reader) · export read-only (chain_hash/saldo intactos) + audit sem
conteúdo financeiro · print view renderiza · volume 60 linhas com queries
< 25 constantes. Regressão: **635 passed**. check limpo. migrations OK.

## JUDGE
[✔] CSV via StatementService · [✔] filtros/moeda/conta respeitados ·
[✔] CSV injection mitigado c/ teste específico · [✔] export limitado +
streaming · [✔] print não cria dados financeiros · [✔] IDOR bloqueado ·
[✔] regressão verde

JUDGE VERDICT: PASS
