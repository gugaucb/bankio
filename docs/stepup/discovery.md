# DISCOVERY REPORT — Step-up Challenge customer-facing (Fase 0)

Data: 2026-08-23 · Baseline: main @ df69604, 460 testes verdes.

**Challenge model:** `apps/fraud/models.py:183-209` — `RiskChallenge(customer, evaluation→RiskEvaluation, material_hash sha256-64, code_hash sha256-32, status, created_at, expires_at, verified_at)`. Status: PENDING/VERIFIED/CONSUMED/EXPIRED. Índice `(customer, status)`.

**Challenge services:** `apps/fraud/challenge.py` — `material_hash(*facts)`, `_facts_digest` (dict ordenado), `issue_challenge(evaluation, customer, material_facts)` → retorna `(challenge, code_plaintext)`, `verify_challenge(challenge, code, material_facts)` (expira se due; rejeita não-pending = replay; material divergente → EXPIRED + MATERIAL_CHANGED; código errado → INVALID_CODE mantendo PENDING; sucesso → VERIFIED), `consume_challenge(challenge, operation_reference)` (só de VERIFIED; muta material_hash para `("consumed", ref)` → CONSUMED). TTL 10 min (`CHALLENGE_TTL_MINUTES`). **Não alterar.**

**Challenge states:** PENDING → VERIFIED (verify ok) → CONSUMED (consume); PENDING/qualquer → EXPIRED (TTL ou material change). "Cancelado" não existe.

**Current code delivery:** `issue_challenge` devolve o código em memória. Transfers descarta (`ch, _code`, services.py:108). Payments/cards nem emitem — usam `gate.enforce()` que levanta `RiskGateIntervention("STEP_UP_REQUIRED")` sem challenge.

**Transfer flow:** `apps/transfers/views.py:11-51` POST `/transfers/` (HTML + HTMX) → `execute_transfer` (services.py:151): idempotência por key (replay retorna row existente SEM reentrar no gate, :168-170) → `_risk_gate` (:86-134): avalia (fail-open audited), CHALLENGE efetivo → `issue_challenge(facts={"amount","beneficiary","idempotency_key"})` + `TransferError("STEP_UP_REQUIRED", "...(challenge {pk})")`; BLOCK enforcement → cria Transfer FAILED + audit. Settlement em `_execute_transfer_atomic` com select_for_update, limites, saldo por ledger, journal balanceado.

**Payment flow:** `apps/payments/services.py::pay_bill` — idempotency_key própria (Payment.idempotency_key), avaliação BILL_PAYMENT fail-open, `enforce(ev)` → PaymentError(g.action). Sem emissão de challenge.

**Card flow:** `apps/cards/services.py::purchase` — idempotência via ledger marker `card-purchase:{key}` checada após lock do cartão; hard controls → decline() grava CardTransaction declined; depois `enforce(ev)` → `decline(g.action)`. Sem emissão de challenge.

**Idempotency mechanisms:** Transfer/Payment rows com idempotency_key; ledger `find_idempotent/record_idempotent` (caller-owned); RiskEvaluation.idempotency_key indexada correlaciona avaliação↔operação. `_post_gate_review_flag` já lê a avaliação pela key (:137-148).

**Notification infrastructure:** apenas `apps/notifications/models.py::Notification` (recipient/category/title/body/read) in-app + context_processor. **Sem canal externo (SMS/e-mail).** body persiste em texto → NÃO serve para o código (nunca persistir plaintext).

**Audit mechanism:** `apps/audit/services.record(actor, action, request, resource, metadata)` — AuditLog imutável. Padrões existentes: ADMIN_USER_*, TRANSFER_FAILED, RISK_EVALUATION_ERROR, FRAUD_MODE_CHANGED. Metadata truncável; sem segredos.

**Existing templates reusable:** `templates/dashboard/shell.html` (área logada), `dashboard/security.html`, `transfers/index.html` + `_result.html` (HTMX), padrão de partials `_*`.

**Risks:**
1. Reavaliar a operação na retomada pode re-gerar CHALLENGE (loop) — resolver apresentando o challenge verificado ao gate.
2. Destination account não consta nos fatos materiais atuais → troca de destino passaria despercebida; estender fatos (source/destination) na emissão é seguro (testes de challenge usam fatos próprios).
3. Concorrência verify/consume: verificar+consumir precisa de lock de linha para garantir 1 settlement.
4. Código nunca pode parar em HTML/query/AuditLog/DB plaintext → canal simulado via logger, Notification apenas avisa "código enviado".
5. `execute_transfer` regenera key quando header ausente → fluxo de retomada deve carregar a mesma key.

**Files expected to change/add:**
- B1: `apps/fraud/challenge_delivery.py` (novo), `apps/fraud/challenge_views.py` (novo), `apps/identity/urls.py` (rotas /security/challenge/*), `templates/security/challenge.html` (novo), emissão em `transfers/services.py` passa a entregar via delivery + audit.
- B2: `transfers/services.py` (_risk_gain aceita step_up_code/challenge_id; fatos estendidos), `transfers/views.py` (superfície de retomada), testes novos.
- B3: `payments/services.py`, `cards/services.py` + views correspondentes.
- B4: `apps/fraud/challenge_security.py` (tentativas/cooldown/reissue/locking wrapper), auditoria CHALLENGE_*.

**Decisão arquitetural (sem modelo novo):** a intenção pendente NÃO é persistida — o browser re-apresenta o payload original junto do código; a validação criptográfica do material_hash torna qualquer adulteração fail-closed (MATERIAL_CHANGED). Justificativa: RiskEvaluation não armazena destination/source completos; duplicar intenção financeira criaria segunda fonte de verdade e risco de divergência; idempotency_key garante settlement único mesmo sob double-submit.
