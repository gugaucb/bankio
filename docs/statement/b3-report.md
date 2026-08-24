# S5.B3 — feat/account-statement-filters — Judge Report

## DESIGN
- `apply_filters(qs, account, params)` no StatementService: TODOS os filtros
  server-side sobre a MESMA queryset do ledger (nenhuma query paralela).
- Período: today/7d/30d/month/custom(from,to). Datas inválidas ou intervalo
  invertido → filtro ignorado com segurança (nunca escopo mais largo que o
  pedido — degrada para histórico completo autenticado da própria conta).
- Direção IN/OUT derivada dos sides reais da conta (CREDIT p/ LIABILITY),
  não heurística textual.
- Tipo: TRANSFER/PAYMENT/CARD via subquery journal_id__in nos models reais;
  OTHER = journals sem origem conhecida. Nada inferido por descrição.
- Busca: apenas journal.reference e journal.description (campos realmente
  armazenados), icontains, sempre dentro do ledger_account da conta.
- Paginação preserva filtros nos links. Termo buscado é ecoado apenas no
  input (1 ocorrência), nunca como linha de resultado alheia.

## FILES
- apps/accounts/statement.py — apply_filters.
- apps/identity/app_views.py + templates/dashboard/statement.html — form.
- tests/test_statement_filters.py (+9).

## TESTES
period 7d exclui antigos · custom range · datas inválidas degradam · range
invertido rejeitado · direction=out · busca por descrição · busca não vaza
journal de outra conta (count==1, só echo) · source OTHER/TRANSFER ·
paginação preserva period. Regressão: **621 passed**. check limpo.

## JUDGE
[✔] mesma queryset do service · [✔] tipos por relação real, sem heurística ·
[✔] busca limitada a campos indexáveis da conta · [✔] input inválido seguro ·
[✔] regressão verde

JUDGE VERDICT: PASS
