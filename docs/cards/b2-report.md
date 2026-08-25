# FASE 8 B2 — feat/cards-controls-lifecycle — Judge Report

## DESIGN
- Controles customer-facing POST-only na rota /app/cards/<id>/controls/
  (freeze/unfreeze/toggle_online/toggle_international/report_lost);
  ownership server-side (404), CSRF, redirect seguro, ação desconhecida
  ignorada com mensagem. Serviços de domínio existentes REUTILIZADOS
  (set_card_control/freeze/unfreeze/report_lost) — nada recriado.
- Estados reais respeitados: FROZEN↔ACTIVE; BLOCKED terminal (unfreeze
  recusa; set_card_control recusa mudanças em BLOCKED).
- Replacement: request_card sobre conta com cartão BLOCKED audita
  CARD_REPLACEMENT_REQUESTED (mesmo fluxo CardRequest existente).
- Compras históricas nunca migradas para novo cartão.

## BUG PRÉ-EXISTENTE CORRIGIDO
`set_card_control` aceitava status=BLOCKED mas o ignorava silenciosamente —
**report lost NUNCA bloqueava o cartão** (ele permanecia ACTIVE e
comprável!). Corrigido: BLOCKED agora é transição válida one-way. Testes
legados continuam verdes.

## FILES
- apps/cards/services.py (fix BLOCKED + audit replacement em request_card)
  · identity/app_views.py (+card_control_view POST-only) · identity/urls.py
  (+app_card_control) · card_detail.html (+card Controls) ·
  tests/test_cards_controls_ui.py (+10).

## TESTES
freeze/unfreeze via POST · toggles online/intl · lost é terminal (service
recusa unfreeze) · GET não destrutivo · IDOR 404 sem alterar alheio ·
ação desconhecida segura + CsrfViewMiddleware presente · double freeze
idempotente · frozen recusa compra / unfrozen compra · replacement
auditado após lost · CARD_UPDATED auditado.

## GATES
make verify: **729 passed** · check limpo · migrations OK.

JUDGE: [✔] serviços reutilizados [✔] POST/CSRF/ownership [✔] máquina de
estados real [✔] bug lost corrigido [✔] adversarial verde

JUDGE VERDICT: PASS
