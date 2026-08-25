# FASE 8 B5 — feat/cards-billing-cycle — Judge Report

## DESIGN
- CreditStatement ESTENDIDO (due_date, migration 0003) — nenhum model
  paralelo de fatura criado.
- billing.py: ciclo determinístico mês-calendário anterior
  (period_start/period_end server-side, timezone-aware via __date);
  fatura DERIVADA das CardTransactions elegíveis (declined=False,
  journal não nulo, dentro do período) — zero cópia de movimentos.
- Idempotência: unique(card, period_end) + get_or_create; fechar 2–3×
  nunca duplica/reescreve. Zero compras → nenhuma fatura vazia.
- Compra após fechamento cai no próximo ciclo; histórico fechado imutável.
- Estornos posteriores: não existem no domínio (documentado); regra
  futura definirá ajuste sem reescrever snapshot.
- Comando manage.py close_card_invoices [--reference AAAA-MM-DD]
  (sem Celery), audit CARD_INVOICE_CLOSED por fatura criada.

## FILES
- apps/cards/models.py (+due_date) · migrations/0003 · apps/cards/billing.py
  (novo) · management/commands/close_card_invoices.py (novo) ·
  tests/test_cards_billing_cycle.py (+8).

## TESTES
compra entra no ciclo correto (datas mockadas timezone-aware) · fechamento
idempotente 3× · compra pós-fechamento → próximo ciclo + histórico
imutável + open_cycle_total correto · zero compras → zero faturas ·
múltiplos cartões/clientes independentes · declined nunca compõe fatura ·
composição explica o total (A+B=25) · comando roda e audita.

## GATES
make verify: **750 passed** · check limpo · migrations OK.

JUDGE: [✔] estende em vez de duplicar [✔] datas server-side [✔] fechamento
idempotente [✔] derivada de fonte única [✔] comando padrão do projeto

JUDGE VERDICT: PASS
