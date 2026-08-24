# BRANCH 3b — feat/step-up-card-resume — Implementation & Judge Report

## OBJECTIVE
Retomada de Card Purchase após challenge válido, sem alterar regras financeiras nem semântica do ledger.

## DESIGN
Gate de risco movido para fora da transação atômica (INV 9 — a evidência/challenge sobrevive à compra abortada); controles rígidos e settlement permanecem sob lock do cartão (`_purchase_atomic`). Fatos materiais: `{amount, card, merchant, idempotency_key}`. Engine re-executa a cada tentativa: challenge confirmado NUNCA bypassa BLOCK fresco em ENFORCEMENT (testado). Replay com mesma key retorna a mesma transação sem reentrar no gate; checagem autoritativa segue dentro do lock.

## FILES
- `apps/cards/services.py` — `purchase` dividido em gate externo + `_purchase_atomic`; emissão/confirmação de challenge; `resume_purchase()` novo.
- `tests/test_step_up_card_resume.py` — 5 testes novos.

## TESTS
challenge interrompe com declined row + notificação · resume liquida exatamente uma vez + replay idempotente · código errado zero movimento · merchant adulterado → MATERIAL_CHANGED+EXPIRED+zero movimento · challenge confirmado não bypassa ENFORCEMENT BLOCK fresco (RISK_BLOCKED).
Regressão: **493 passed**. check/makemigrations limpos.

## JUDGE
[✔] backend reutilizado · [✔] card retoma uma única vez · [✔] Risk Engine respeitado na conclusão
[✔] regras financeiras e ledger intactos · [✔] regressão verde

JUDGE VERDICT: PASS
