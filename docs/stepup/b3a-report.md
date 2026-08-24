# BRANCH 3a — feat/step-up-payment-resume — Implementation & Judge Report

## OBJECTIVE
Retomada de Bill Payment após challenge válido, sem alterar regras financeiras nem semântica do ledger.

## DESIGN
Fatos materiais do payment são **totalmente deriváveis server-side** (bill/account imutáveis + idempotency_key), então o payload retomado não carrega valores adulteráveis: `resume_payment` reconstrói os fatos do banco e o gate revalida contra o material_hash. Engine roda em toda tentativa — challenge confirmado nunca bypassa BLOCK fresco. Emissão movida para fora da transação de settlement (INV 9).

## FILES
- `apps/payments/services.py` — `pay_bill` com step-up (issue+STEP_UP_REQUIRED com `.challenge_id/.facts`; confirm locked quando código presente); `resume_payment()` novo.
- `tests/test_step_up_payment_resume.py` — 5 testes novos.

## TESTS
challenge interrompe e notifica sem liquidar · resume liquida exatamente uma vez (saldo conferido) + double-submit idempotente · código errado zero movimento · bill trocada no payload → MATERIAL_CHANGED+EXPIRED+zero movimento · concorrência 2 threads ≤1 settlement.
Regressão: **488 passed**. check/makemigrations limpos.

## JUDGE
[✔] backend reutilizado (guard/challenge/gate) · [✔] nenhuma segunda implementação
[✔] payment retoma uma única vez · [✔] Risk Engine não é ignorado na conclusão
[✔] regras financeiras e ledger intocados · [✔] regressão verde

JUDGE VERDICT: PASS
