# FASE 8 B1 — feat/cards-advanced-dashboard — Judge Report

## DESIGN
- Detail /app/cards/<id>/ com ownership server-side (filtro
  account__customer=request.user → 404 indistinguível).
- Limite de crédito DERIVADO via novos helpers em services
  (credit_used/credit_availability/outstanding_statement_total) — sem nova
  coluna mutável; fonte única: CardTransaction aprovadas − statements pagos.
- Lista existente preservada, apenas link "View details" adicionado.
- Nenhum dado sensível: só masked_number/last4, status, limites, invoice
  aberta (soma statements unpaid), controles read-only, últimas transações.

## FILES
- apps/cards/services.py (+3 helpers derivados) · identity/app_views.py
  (+card_detail_view @login_required @customer_only) · identity/urls.py
  (+app_card_detail) · templates/dashboard/card_detail.html (novo) ·
  cards.html (+link) · tests/test_cards_dashboard.py (+5).

## INCIDENT CORRIGIDO NO BRANCH
Inserção da view roubou os decorators de cards_view (anonymous 500 /
manager 200). Detectado pela regressão antiga (test_app_pages), corrigido —
prova do valor da suíte existente.

## TESTES
detail mostra last4 mascarado, limites total/usado/disponível corretos após
compra real · IDOR outro usuário → 404 · anônimo → 302 · sem CVV/PIN/PAN ·
lista linka detail.

## GATES
make verify: **719 passed** · check limpo · migrations OK.

JUDGE: [✔] reuso [✔] zero dados sensíveis [✔] ownership server-side
[✔] design preservado [✔] regressão antiga verde

JUDGE VERDICT: PASS
