# BRANCH 2 — feat/step-up-transfer-resume — Implementation & Judge Report

## OBJECTIVE
Retomada segura da transferência após challenge válido, sem reconstruir o domínio.

## DESIGN (justificação arquitetural — sem modelo novo)
A intenção pendente NÃO é persistida. O payload original viaja no round-trip do cliente e é **revalidado criptograficamente** contra o material_hash dentro do gate; qualquer adulteração → MATERIAL_CHANGED + challenge EXPIRED. Motivos: (a) RiskEvaluation não armazena source/destination completos; (b) duplicar a intenção criaria segunda fonte de verdade; (c) idempotency_key garante settlement único mesmo sob double-submit.

## FILES
- `apps/fraud/challenge_guard.py` (novo) — `confirm_and_consume()` com SELECT FOR UPDATE (1 challenge → ≤1 consumo) + `confirm()` que faz o pre-check de material FORA da transação para o tombstone EXPIRED sobreviver ao rollback.
- `apps/transfers/services.py` — fatos materiais estendidos para `{amount, beneficiary, source_account, destination_account, idempotency_key}` (fecha troca de destino pós-emissão); `_risk_gate` aceita `step_up_code/challenge_id`: código correto satisfaz o CHALLENGE exatamente uma vez; sem código → issue+STEP_UP_REQUIRED com `.challenge_id`/`.facts` canônicos anexados ao erro; BLOCK em enforcement continua bloqueando mesmo com código apresentado; `resume_transfer()` nova entrada pública.
- `apps/fraud/challenge_views.py` — POST com payload retomável despacha para `resume_transfer`; erros mapeados para mensagens seguras.
- `apps/transfers/views.py` + `templates/transfers/index.html` — painel de confirmação inline (HTML+HTMX) com os fatos canônicos escondidos; nunca expõe hashes/código.

## FLOW PROVEN BY TESTS
TRANSFER → engine CHALLENGE → RiskChallenge emitida (OOB delivery) → STEP_UP_REQUIRED → código correto → verify+consume locked → settlement UMA VEZ (saldos conferidos por account_balance).

## TESTS (7 novos, `tests/test_step_up_resume.py`)
resume completo single-settlement · código errado zero movimento · tampering MATERIAL_CHANGED+EXPIRED+zero movimento · concorrência 2 threads ≤1 settlement · double-submit idempotente · jornada UI painel→confirmação · UI código errado não liquida. Regressão: **483 passed** (476→483). check/makemigrations limpos.

## JUDGE
[✔] backend reutilizado (challenge.py/models/gate intocados; guard apenas encapsula com lock)
[✔] nenhuma segunda implementação · [✔] material binding intacto e mais forte (source/destination)
[✔] replay falha · [✔] concorrência não duplica operação (teste transacional)
[✔] transferência retoma uma única vez · [✔] CSRF/IDOR herdados do B1
[✔] auditoria CHALLENGE_VERIFIED/CONSUMED sem segredos · [✔] regressão verde · ledger intacto

JUDGE VERDICT: PASS
