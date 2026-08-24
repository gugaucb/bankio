# RESPOSTA.md — Diagnóstico do repositório vs proposta "Step-up Authentication + Central de Segurança"

Data: 2026-08-23 · Base: estado atual de `main` (pós painel administrativo, 460 testes verdes).

Legenda: ✅ implementado · 🟡 parcial / só backend · ❌ não existe.

---

## A. Step-up Challenge (1–30)

**1. Existe interface customer-facing para execução do Step-up Challenge?**
❌ Não. O backend emite o challenge (`apps/transfers/services.py:104-110` chama `issue_challenge`), mas nenhuma view/template consome `verify_challenge`/`consume_challenge` fora dos testes. O próprio `SOBRE.md` (linhas 193-194) registra: "Step-up challenge completo no backend, mas sem tela customer-facing ainda".

**2. Quais arquivos participam do fluxo atual?**
- Modelo: `apps/fraud/models.py` → `RiskChallenge` (linhas 183-209).
- Serviços: `apps/fraud/challenge.py` (`issue_challenge`, `verify_challenge`, `consume_challenge`, `material_hash`).
- Emissão: `apps/transfers/services.py:86-110` (`_risk_gate`).
- Gate genérico: `apps/fraud/gate.py` (`enforce` → `RiskGateIntervention("STEP_UP_REQUIRED")`).
- Métricas: `apps/fraud/challenge_metrics.py`.
- Testes: `tests/test_fraud_step_up.py`, `tests/test_fraud_challenge_gate.py`, `tests/test_fraud_adversarial.py:61-91`, `tests/test_fraud_challenge_metrics.py`.
- **Não há forms, URLs, views ou templates.**

**3. Existe URL `/security/challenge/<token>/` ou equivalente?**
❌ Não. Nenhuma rota de confirmação de desafio existe (`apps/identity/urls.py`, `apps/fraud/urls.py`, `apps/transfers/urls.py`).

**4. O código de 6 dígitos pode ser informado em alguma tela existente?**
🟡 Apenas para OTP de login (`templates/auth/otp.html` + `apps/identity/views.py:37-54`). Não há tela para o código do step-up challenge; além disso o código emitido é descartado no emissor (`ch, _code = issue_challenge(...)`, transfers/services.py:108).

**5. O backend possui serviço completo de criação/validação/consumo/expiração?**
✅ Sim — `apps/fraud/challenge.py`: criação com hash de código e material, validação de código + material, consumo vinculado à operação, expiração preguiçosa (`_expire_if_due`) e por TTL.

**6. Como o estado é representado?**
`RiskChallenge.Status`: `PENDING`, `VERIFIED`, `CONSUMED`, `EXPIRED` (`apps/fraud/models.py:190-194`). "Cancelado" e "inválido" não existem como estados explícitos — material alterado converte o challenge para `EXPIRED`; replay retorna erro `CHALLENGE_NOT_PENDING`.

**7. É single-use?**
✅ Sim. `verify_challenge` exige status PENDING; sucesso → VERIFIED; `consume_challenge` exige VERIFIED e muta `material_hash` para `material_hash("consumed", operation_reference)`. Coberto por `tests/test_fraud_step_up.py::test_challenge_cannot_be_reused_for_second_operation` e `tests/test_fraud_adversarial.py::test_replayed_and_tampered_challenges_fail`.

**8. TTL de 10 minutos implementado e testado?**
✅ Sim. `CHALLENGE_TTL_MINUTES = 10` (challenge.py:16), `expires_at = now + 10min` (:43) e `tests/test_fraud_step_up.py::test_expired_challenge_rejected`.

**9. O código é armazenado somente como hash?**
✅ Sim. `code_hash = sha256(code)[:32]` (challenge.py:42); comparação por hash na verificação (:60). Mesmo padrão do OTP (`apps/identity/services.py:38-53`).

**10. Proteção contra reutilização de challenge consumido?**
✅ Sim — máquina de estados + teste adversarial de replay (ver item 7).

**11. `material_hash` está implementado?**
✅ Sim. `challenge.py:23-32` — SHA-256 sobre os fatos (dict ordenado por chave ou lista).

**12. Quais fatos entram no material_hash hoje?**
Somente no fluxo de transferência (`transfers/services.py:106-107`): `amount`, `beneficiary` (pk) e `idempotency_key`.

**13. Alterar valor/beneficiário após emissão invalida automaticamente?**
✅ Sim, na camada de serviço: divergência de digest → challenge vira EXPIRED + `ChallengeError("MATERIAL_CHANGED")` (challenge.py:55-59, "INV 5"). Porém como não há fluxo real que verifique, a proteção só atua se alguém chamar `verify_challenge`.

**14. Teste automatizado disso?**
✅ `tests/test_fraud_step_up.py::test_material_change_after_issuance_kills_challenge` e `tests/test_fraud_adversarial.py::test_amount_tamper_after_issuance_kills_challenge`.

**15. Mecanismo de reenvio de challenge?**
❌ Não existe.

**16. Rate limiting/cooldown de reenvio?**
❌ Não (não há reenvio).

**17. Limite de tentativas incorretas?**
❌ Não. `INVALID_CODE` mantém o challenge PENDING indefinidamente até expirar (teste `test_wrong_code_rejected_and_challenge_still_pending`). Brute force só é contido pelo TTL de 10 min.

**18. Rate limit para validação do código?**
❌ Não.

**19. Tentativas geram AuditLog?**
❌ Não. `challenge.py` não grava auditoria alguma (nem emissão, nem sucesso, nem falha). A única trilha é a `RiskEvaluation` associada.

**20. Metadados registrados hoje nos challenges?**
Nenhum campo de metadata no modelo; apenas customer, evaluation FK, hashes, status e timestamps. Auditoria indireta via `RiskEvaluation.triggered_rules/signal_values`.

**21. Possibilidade de segredo aparecer em logs/AuditLog?**
🟡 Baixa, mas real: o código nunca é logado nem auditado (bom). Risco residual: a mensagem de erro de transferência embute `challenge {id}` (`STEP_UP_REQUIRED ... (challenge {ch.pk})`, services.py:110) — id, não código. OTP de login: comentário indica demo expondo código "via mailhog-style log" (services.py:90).

**22–24. Retomada de transferência/pagamento/compra após STEP_UP_REQUIRED?**
❌ Nenhuma das três. Transferência levanta `TransferError("STEP_UP_REQUIRED")` (não persiste rascunho retomável); pagamento levanta `PaymentError(g.action)` (payments/services.py:32-33); compra de cartão faz `decline(g.action)` gravando CardTransaction declined (cards/services.py:118-123). Nenhum endpoint de continuação.

**25. Reavaliação pelo Risk Engine após challenge aprovado?**
❌ Não aplicável — não há aprovação consumível em fluxo real; nada re-submete a operação ao engine.

**26. Challenge aprovado autorizar operação diferente?**
🟡 O design previne (binding por `material_hash` + `consume_challenge(operation_reference)` + INV 5 testado), mas como não existe fluxo de continuação, a proteção é latente, não exercida em produção.

**27. Idempotência na continuação pós-challenge?**
❌ Não implementada (não há continuação). A idempotência existente é da operação original (`idempotency_key` em transfer/payment/card).

**28. Testes de concorrência (dois requests consumindo o mesmo challenge)?**
❌ Não para challenges (existe teste de corrida para block/unblock administrativo em `tests/test_admin_regression.py:168-191`, outro domínio).

**29. Testes adversariais de replay?**
✅ `tests/test_fraud_adversarial.py::test_replayed_and_tampered_challenges_fail` (replay do código válido falha) + tampering de valor.

**30–... (referências cruzadas)**

---

## B. Login, dispositivos e sessões (30–45)

**30. Área de segurança customer-facing no app identity?**
🟡 Existe `GET/POST /app/security/` (`apps/identity/app_views.py:264+`, template `templates/dashboard/security.html`) — mas contém **apenas troca de senha**.

**31. Página de dispositivos conhecidos?**
❌ Não.

**32. Sistema registra dispositivos?**
✅ Parcialmente: `register_device()` em `apps/identity/services.py:22-29`, chamado a cada login bem-sucedido (services.py:87). Modelo `Device` em `apps/identity/models.py:47-56`.

**33. Informações armazenadas por dispositivo?**
`user`, `device_id` (SHA-256 de User-Agent + Accept-Language, services.py:16-19), `name` (User-Agent truncado), `trusted` (BooleanField, default False), timestamps.

**34. Distinção conhecido/novo/primeiro/compartilhado?**
🟡 Só parcialmente: `is_new_device()` (services.py:32-35) considera novo todo device sem flag `trusted=True` — como `register_device` cria com `trusted=False` e **nada marca trusted=True**, todo device é sempre "novo". Primeiro dispositivo e dispositivo compartilhado não são distinguidos em lugar algum.

**35. Cliente remove/revoga dispositivo?**
❌ Não.

**36–39. Gerenciamento de sessões ativas (visualizar/encerrar uma/todas as outras)?**
❌ Nada customer-facing. Django usa sessões DB, mas não há UI/serviço de listagem ou encerramento seletivo. (Bloqueio admin mata todas as sessões do usuário — item 40.)

**40. Bloqueio/ações de segurança invalidam sessões?**
✅ Sim para bloqueio admin: `_kill_sessions(user)` em `apps/identity/admin_services.py:53` (scan de sessões DB por `_auth_user_id`), chamado por `block_user`; testado em `tests/test_admin_regression.py::test_blocked_session_immediately_dead`. Troca de senha chama `update_session_auth_hash` (app_views.py:271).

**41. Histórico customer-facing de atividades de segurança?**
❌ Não. Existem eventos no AuditLog (LOGIN, LOGIN_FAILED, LOGIN_MFA, LOGOUT, PASSWORD_CHANGED), mas nenhuma página os expõe ao cliente.

**42. Página “Minha Conta → Segurança”?**
🟡 `/app/security/` existe, porém só com formulário de senha.

**43. Mostra quando a senha foi alterada pela última vez?**
❌ Não (só o evento PASSWORD_CHANGED no AuditLog).

**44. Mostra status de OTP/MFA?**
❌ Não na UI. Campo `mfa_enabled` existe no User.

**45. Fluxo de ativação/desativação/recuperação de OTP pelo cliente?**
❌ Não. MFA só via seed/admin direto no banco.

---

## C. MFA/OTP (46–47)

**46. Mecanismos de MFA efetivamente implementados?**
✅ Um só: OTP numérico de 6 dígitos no fluxo de login (`attempt_login` → `generate_otp` → `/otp/` → `verify_otp`; apps/identity/services.py:38-95, views.py:37-54). Armazenado como hash de 12 hex chars, uso único, entrega out-of-band simulada. Sem TOTP/WebAuthn/SMS real.

**47. OTP e Step-up Challenge compartilham infraestrutura?**
❗ São mecanismos **separados**: OTP vive em identity (hash no User, validade nominal 5 min sem timestamp real); challenge vive em fraud (RiskChallenge, TTL 10 min real, material binding). Não compartilham modelo, serviço nem template.

---

## D. Risk Engine × autenticação (48–81)

**48. Risk Engine roda durante o login?**
❌ Não. `login_view`/`attempt_login` não invocam `evaluate_login`. Grep confirma: `evaluate_login` só aparece em `apps/fraud/auth_risk.py` e `tests/test_fraud_auth_risk.py`.

**49. operation_type usado para risco no login?**
"LOGIN" — definido em `auth_risk.py:13` e policy `POLICIES["LOGIN"]` (policies.py:24-29), mas usado somente em avaliação isolada/testes.

**50. Resultado interfere no acesso?**
❌ Não — apenas registrado (modo SHADOW no teste `test_evaluate_login_records_and_shadow_does_not_interfere`).

**51. Modo do login?**
🟡 Equivalente a **SHADOW observacional não-conectado**: o serviço existe, o modo global default do engine é SHADOW (`modes.py:20-26`), mas o login nem chega a avaliar.

**52. BLOCK impede autenticação?** ❌ Não (fluxo não integrado).
**53. CHALLENGE apresenta fluxo adicional no login?** ❌ Não.
**54. REVIEW tem comportamento no login?** ❌ Não.

**55. Fail-safe de login integrado ao fluxo?**
🟡 Matriz definida e versionada (`apps/fraud/failsafe.py`: LOGIN ∈ FAIL_CLOSED, unknown→FAIL_CLOSED), porém **nenhum código de autenticação a consulta**.

**56. Falha interna do engine no login resulta fail-closed?**
❌ Não aplicável — engine não roda no login.

**57. Testes de fail-closed do login?**
❌ Não. Só a matriz declarativa (failsafe-v1) e seus conceitos; nenhum teste de autenticação a exercita.

**58–59. Engine na alteração de senha? interfere?**
🟡 Executa em modo observação: `security_view` chama `evaluate_profile_change(..., operation_type="PASSWORD_CHANGE")` (app_views.py:274-279) dentro de try/except pass — resultado **descartado**, nunca interfere. Nunca fatal.

**60–61. Engine na alteração de perfil? interfere?**
🟡 Serviço `evaluate_profile_change` suporta PROFILE_UPDATE (`apps/fraud/profile_risk.py`), mas **nenhum view de perfil o chama** (grep: só password change chama, com op PASSWORD_CHANGE). Não interfere em nada.

**62. Avaliação específica para cadastro/reconhecimento de novo dispositivo?**
❌ Não. Novo dispositivo não gera avaliação nem sinal conectado (ver 67).

**63. Sinais realmente conectados ao fluxo de login hoje?**
Nenhum sinal de risco. No fluxo de login propriamente dito rodam apenas: lockout por tentativas (MAX_FAILED=5, 15 min, services.py:12-13), MFA opcional, registro de Device, AuditLog LOGIN/LOGIN_FAILED.

**64. Sinal IP diferente coletado em produção/dev?**
🟡 O coletor existe (`signals_auth.py:12-24`, baseline via AuditLog LOGIN anterior), mas só roda quando uma avaliação LOGIN acontece — ou seja, nunca no fluxo real.

**65. Velocity de login com eventos reais?**
🟡 Mesmo caso: `LOGIN_VELOCITY_15MIN` conta AuditLog LOGIN/LOGIN_FAILED reais (signals_auth.py:27-41), porém desconectado do login.

**66. Falhas de MFA alimentam sinal?**
🟡 `MFA_FAILURE_COUNT_24H` existe (signals_auth.py:46-55) e comenta que falhas viram LOGIN_MFA com flag de metadata; desconectado do fluxo.

**67. Novo dispositivo aumenta score/dispara regra?** ❌ Não (nenhuma regra consome `is_new_device`).
**68. Dispositivo compartilhado aumenta score?** ❌ Não (conceito inexistente).

**69. Regras ativas para Account Takeover?**
🟡 Componente `correlate_account_takeover` (apps/fraud/ato.py): janela ATO_WINDOW_HOURS=48, conta fatores distintos de AuditLog + RiskEvaluation, com testes (`tests/test_fraud_ato.py`). Mas...

**70. Correlação ATO integrada ao login?**
❌ Não — componente isolado; grep mostra uso apenas em seu teste.

**71. Policies diferentes LOGIN/PROFILE_UPDATE/PASSWORD_CHANGE?**
🟡 LOGIN sim (allow/challenge/challenge/block), PROFILE_UPDATE sim (allow/allow/challenge/review) — policies.py:21-37. **PASSWORD_CHANGE não tem policy própria** (cai no DEFAULT).

**72. Decisões por nível para LOGIN:** LOW→ALLOW, MEDIUM→CHALLENGE, HIGH→CHALLENGE, CRITICAL→BLOCK.
**73. Para PROFILE_UPDATE:** LOW/MEDIUM→ALLOW, HIGH→CHALLENGE, CRITICAL→REVIEW.
**74. Policy específica de senha?** ❌ Não (DEFAULT fallback).
**75. Versionamento/histórico auditável das policies?**
🟡 Constante `POLICY_VERSION="policy-v1"` carimbada nas RiskEvaluation (histórico imutável de decisões ✔), mas as policies em si são dicts em código, sem histórico de mudanças em DB. Regras (RiskRule) têm versionamento/lifecycle completos (`rule_versioning.py`, `rule_management.py`).

**76. Backtest para regras de autenticação?**
🟡 Infraestrutura geral existe (`backtesting.py`, `false_positives.py`, relatório `docs/fraud/shadow-backtest-report.md`), focada em TRANSFER/CARD/PAYMENT — nada específico de LOGIN.

**77. Métricas shadow de ALLOW/CHALLENGE/REVIEW/BLOCK em logins?** ❌ Não (logins não são avaliados).
**78. Dados suficientes p/ falsos positivos antes do enforcement de login?** ❌ Não.
**79–80. Etapa OBSERVE→… concluída/aprovada para login?**
❌ Nenhuma. As etapas formalmente concluídas foram para Transfer/CARD/BILL_PAYMENT (tags `bankio-fraud-engine-*-v*`, SOBRE.md linhas 177-179; enforcement total de transfer documentado em linha 154). Login nunca entrou na esteira.

**81. Relatórios/documentos de aceite sobre enforcement em autenticação?**
❌ Não. Docs existentes cobrem fraude monetária (`docs/fraud/*`) e painel admin (`docs/admin/*`).

---

## E. Painel administrativo & console de fraude (82–104)

**82. Painel admin tem área de segurança/risco?** ❌ Não — dashboard só tem métricas de usuários.
**83. Exibe bloqueados?** ✅ Sim — card "Blocked" (`admin_dashboard_stats`, admin_services.py:185-193).
**84. Challenges últimas 24h?** ❌ (a função `challenge_metrics(window_hours=24)` existe em fraud e poderia alimentar isso).
**85. Falhas de MFA?** ❌.
**86. Novos dispositivos?** ❌.
**87. Logins de risco alto/crítico?** ❌.
**88. Usuários/operações em REVIEW?** ❌ no painel admin (o console de fraude conta REVIEW no dashboard dele, fraud/views.py:55).

**89. Visão consolidada de eventos de segurança por usuário?** ❌ Não.
**90. Admin abre usuário e vê avaliações de risco dele?** ❌ (`user_detail.html` só dados cadastrais + block/unblock).
**91. Admin vê dispositivos de um usuário?** ❌.
**92. Admin vê sessões ativas de um usuário?** ❌ (só consegue matar todas via block).
**93. Admin vê AuditLogs de segurança daquele usuário?** ❌ (dashboard mostra feed global de ADMIN_USER_*, sem filtro por alvo).

**94. Console de fraude já oferece essas infos?** 🟡 Parcialmente: contagens globais de decisões (incl. CHALLENGE/REVIEW/BLOCK), fila de alertas/cases por cliente, distribuição de risco, top regras. Não há visão por-usuário consolidada, dispositivos, sessões ou MFA.

**95. Informações reutilizáveis do console de fraude?**
Modelos/queries de `RiskEvaluation` (por customer FK), `FraudAlert` (customer), `FraudCase`, `challenge_metrics()`, RBAC `has_permission`, padrão visual `templates/fraud/base.html`+`dashboard.html`.

**96. Integração identity+audit+fraud em timeline única por usuário?**
❌ Não existe serviço unificador. (Aproximação mais próxima: correlação ATO lê AuditLog + RiskEvaluation juntos.)

**97. Serviço p/ últimas RiskEvaluation de um usuário?**
🟡 Não há função dedicada; relacionamento inverso existe (`user.risk_evaluations`, related_name em models.py:49) e o console agrega globalmente.

**98. Serviço p/ últimos eventos de autenticação?** 🟡 Não dedicado; sinais consultam AuditLog diretamente (signals_auth.py).
**99. Serviço p/ challenges pendentes/recentes?** 🟡 `challenge_metrics()` dá agregados globais; queries por-customer seriam triviais (`related_name="risk_challenges"`).

**100. Admin força logout / revoga sessões?** 🟡 Indireto: `block_user` mata todas as sessões (mas exige bloquear o usuário).
**101. Revogar dispositivos confiáveis?** ❌.
**102. Exigir novo MFA no próximo login?** ❌.
**103. “Forçar step-up no próximo login”?** ❌.
**104. Bloqueio admin integra-se à autenticação e ao Risk Engine?**
🟡 À autenticação sim (is_active=False rejeita authenticate + sessões mortas). Ao Risk Engine não (bloqueio não gera avaliação/alerta).

**105. Diferença explícita admin-blocked vs risk-blocked?**
🟡 São mecanismos distintos (User.is_active vs decisão BLOCK/RISK_BLOCKED em transferências), mas não há marcador/código unificado que distinga formalmente um "bloqueio por risco" de identidade.

**106. Alguma implementação confunde bloqueio de identidade com bloqueio de conta/cartão/AML/legal hold?**
✅ Não — estão separados: AccountStatus (accounts), decline flags (cards), compliance próprio, restrições managerops, is_active (identidade). Testes de invariantes garantem não-interferência.

**107. Testes: ação de segurança nunca altera saldo/ledger?**
✅ `tests/test_admin_regression.py::TestFinancialInvariants::test_admin_ops_leave_ledger_untouched`.
**108. JournalEntry POSTED imutável nos fluxos de segurança?**
✅ `test_posted_journal_immutable_after_admin_ops` (+ suíte do ledger).
**109. BLOCK de autenticação sem movimentação financeira?**
🟡 Não existe BLOCK de autenticação; os invariantes cobertos garantem que ops admin/segurança não tocam ledger (itens 107-108).

**110–111. Maker-checker para ações administrativas críticas de identidade?**
❌ Não. Maker-checker existe apenas em managerops (fila `ApprovalRequest` + `authority.can_approve`, apps/managerops/views.py:177-289) para operações de gestor; create/block/unblock de usuários são single-operator.

**112. Ações protegidas por RBAC específico?**
✅ Painel admin: `require_admin`/`_require_admin` (Role.ADMIN + superuser bypass). Fraude: `_require_fraud_user` + `rbac.has_permission` (manage_policies etc.).

**113. Role administrativa dedicada a identidade/segurança?**
🟡 `Role.ADMIN` é usada como papel do painel de usuários (decisão D-A01). Não existe role "SECURITY".

**114. MANAGER/FRAUD_* possuem permissões sobre identidade?**
❌ Não — `require_admin` aceita só ADMIN/superuser; fraude tem RBAC próprio para o console /fraud/*.

**115. Risco de duplicar permissões criando nova role?**
Baixo/moderado: permissões atuais estão concentradas em dois pontos (`require_admin` e `fraud/rbac.py`), ambos centralizados — nova role pode plugá-los sem duplicação, desde que não se crie um terceiro sistema de RBAC paralelo.

**116. Decorators/mixins reaproveitáveis?**
`require_admin` (admin_services.py:41), `_require_fraud_user` (fraud/views.py:21), `fraud.rbac.has_permission`, `@login_required`, padrão `_c()`/fixtures de Client dos testes admin.

---

## F. Frontend (117–120)

**117. Componentes reutilizáveis para OTP/código de 6 dígitos?**
🟡 Só o form simples `OTPForm` + `templates/auth/otp.html` (input único). Nenhum componente de caixas separadas/auto-advance.

**118. Componentes HTMX p/ timers/reenvio/erro/confirmação?**
🟡 HTMX está no stack e há partials (templates/site/_*, templates/manager/_*, templates/transfers/_result.html), mas nada de countdown/resend.

**119. Template base p/ páginas de autenticação reforçada?**
🟡 `templates/auth/login.html` standalone; `templates/dashboard/shell.html` para área logada; `templates/fraud/base.html` para consoles internos.

**120. Páginas padrão visual p/ futura Central de Segurança?**
✅ Candidatas: `templates/dashboard/security.html` (página-alvo natural), `dashboard/index.html` (cards), `manager/users.html` (listagem/filtros/paginação server-side).

---

## G. Testes (121–137)

**121. Unit tests do Step-up Challenge:** `tests/test_fraud_step_up.py` — fluxo completo, código errado, material alterado, expirado, reuso em 2ª operação, consume exige VERIFIED (6 testes).
**122. Integração:** `tests/test_fraud_challenge_gate.py::test_transfer_under_challenge_only_requires_step_up`; gate em payments/cards nos testes de full/limited enforcement (`test_fraud_full_enforcement.py`, `test_fraud_limited_enforcement.py`); métricas em `test_fraud_challenge_metrics.py`.
**123. Playwright (MFA/OTP/step-up)?** ❌ Projeto não usa Playwright; E2E é Django test client (`tests/test_e2e_journeys.py`, convenção D-A05).
**124. Adversariais de auth/challenge:** `test_fraud_adversarial.py` (replay, tamper de valor, score impossível, smuggling de modo, analyst/rules, mode switch, lifecycle) + `tests/test_admin_regression.py::TestAdversarial`.
**125. Hypothesis:** `tests/test_fraud_scoring.py`, `tests/test_ledger_posting_engine.py`, `tests/test_ledger.py` (scoring e ledger; nada de auth/challenge).
**126. Expiração:** ✅ `test_expired_challenge_rejected`.
**127. Uso único:** ✅ step-up (replay + reuse) e OTP (hash zerado após uso).
**128. Material_hash alterado:** ✅ 2 testes (item 14).
**129. Brute force do código:** ❌.
**130. Rate limiting:** ❌ (só lockout de senha 5/15min, que não é rate limit de challenge).
**131. Sessão revogada:** ✅ `test_blocked_session_immediately_dead`.
**132. Dispositivo novo:** ❌ (só teste unitário de sinal? não — nem isso; `is_new_device` sem teste dedicado).
**133. Dispositivo compartilhado:** ❌.
**134. Login IP diferente:** 🟡 `test_ip_change_signal_true_only_after_baseline` (sinal isolado).
**135. Velocity de login:** 🟡 `test_login_velocity_counts_success_and_failure` (isolado).
**136. Falha do engine durante login:** ❌.
**137. Regressão OTP após mudanças em identity:** 🟡 Fluxo OTP é exercitado indiretamente pelos E2E journeys; não há suíte dedicada de regressão OTP.

---

## H. Gap analysis vs proposta (138–155)

**138. Totalmente implementados:**
- Backend do step-up challenge (emissão/validação/consumo/expiração/single-use/material binding/TTL/hash-only).
- Gate CHALLENGE_ONLY→STEP_UP_REQUIRED em transfer/payments/cards.
- OTP de login com lockout e auditoria básica.
- Registro de devices no login; invalidação de sessões no bloqueio admin.
- Policies LOGIN/PROFILE_UPDATE versionadas (decision map) + failsafe matrix declarativa.
- Métricas de challenge (`challenge_metrics`), backtesting/false-positive infra para ops monetárias.
- Painel admin de usuários (criar/bloquear/desbloquear/dashboard básico) — recém-entregue.

**139. Parcialmente implementados:**
- Área de segurança do cliente (só senha); devices (registrados, nunca confiáveis nem gerenciáveis); serviços de avaliação login/perfil (existem, desconectados); sinais auth (coletam de dados reais, sem consumidor); ATO 48h (componente isolado); policies (sem histórico DB); maker-checker (fora de identidade).

**140. Só backend, sem interface:**
- Step-up challenge inteiro (item 1); `evaluate_login`; `evaluate_profile_change`(PROFILE_UPDATE); `correlate_account_takeover`; `challenge_metrics`; `Device` management.

**141. Modelos/services não conectados a fluxo real:**
- `evaluate_login`, `ato.correlate_account_takeover`, matriz `failsafe.resolve_failure` (ninguém chama), sinais `signals_auth.*`.

**142. Só em testes/código morto:** idem itens acima — praticamente toda a camada de risco de autenticação.

**143. Feature flag/config mantendo desativado:**
- Modo global do engine (`FRAUD_MODE` setting / FraudEngineSetting, default SHADOW) — afeta transfer/payments/cards; para auth não há sequer flag porque não está wired.

**144. Implementado em modo observacional, sem enforcement:**
- PASSWORD_CHANGE risk (roda, resultado descartado); tudo de login (nem observa, pois não roda).

**145. Sem nenhuma implementação:**
- Tela de challenge; retomada pós-challenge; reenvio/cooldown/limites de tentativa; auditoria de challenges; gestão de sessões; revogação de devices; MFA self-service; página de dispositivos/sessões/histórico de segurança; métricas admin de segurança; timeline por usuário; role de segurança; maker-checker de identidade; backtest/metrics de login.

**146–147. Evidências-chave por funcionalidade:** consolidadas nas seções A–G (arquivo:linha citados em cada item).

**148. Funcionalidades não documentadas no SOBRE.md?**
🟡 SOBRE.md já admite a lacuna do challenge (linha 193). Painel administrativo recém-entregue está documentado em MEMORY_ADMIN.md/docs/admin, mas convém verificar se SOBRE.md o menciona.

**149. SOBRE.md desatualizado?**
Pontos a rever: OTP descrito com "validade 5 min" mas sem enforcement de timestamp; `is_new_device` nunca retorna False na prática; PASSWORD_CHANGE policy inexistente (SOBRE não afirma, mas a leitura pode induzir).

**150. Requisitos elimináveis (já atendidos):**
Hash-only de código; TTL 10 min; single-use; binding material; anti-replay; gate STEP_UP_REQUIRED nos três domínios monetários; invalidação de sessões no bloqueio; invariantes financeiras sob ações de segurança.

**151. Só extensão/integração:**
- Conectar `evaluate_login` ao `attempt_login` + aplicar failsafe matrix; consumir `correlate_account_takeover` no login; wire dos sinais; policy PASSWORD_CHANGE; expor `challenge_metrics` no painel admin; tornar Device trusted/revocável; reusar `require_admin`/RBAC de fraude para novas telas.

**152. Do zero:**
- Tela + URL de challenge (`/security/challenge/<token>/`) com retomada da operação; continuation/idempotência pós-challenge; reenvio com cooldown e limite de tentativas; auditoria de challenges; central de segurança (devices, sessões, histórico); gestão de sessões; MFA self-service; visão por-usuário para admins; maker-checker de identidade; métricas/backtest de login.

**153. Obrigatoriamente reutilizar:**
`apps/fraud/challenge.py` (não reimplementar challenge); `RiskChallenge`/`RiskEvaluation`; `gate.enforce`; `policies.py` + POLICY_VERSION; `failsafe.py`; `signals_auth.py`; `challenge_metrics.py`; OTP de identity; `require_admin` e `fraud.rbac`; AuditLog; templates `dashboard/shell.html`/`security.html`; fixtures/helpers de teste admin.

**154. Maiores riscos de regressão:**
- Tocar em `attempt_login`/`login_view` (lockout+OTP+E2E dependem); ordem gate↔transação em transfers/payments (invariante INV 9 — avaliação deve sobreviver a rollback); `consume_challenge` mutando material_hash (single-use); `_kill_sessions` (varre todas as sessões); mudanças em `is_new_device` (hoje sempre True — corrigir pode ativar regras implicitamente).

**155. Menor conjunto incremental que fecha o ciclo de Step-up Authentication:**
1. Endpoint customer-facing `POST /security/challenge/<challenge_id>/` usando `verify_challenge` + `consume_challenge` (backend já pronto).
2. Entrega do código (log/mailhog hoje; SMS/e-mail depois) — hoje `_code` é descartado em transfers/services.py:108.
3. Continuação da operação: persistir intenção (ou re-submeter com o mesmo `idempotency_key`) após VERIFIED, provando binding por material_hash.
4. Wire mínimo do risco no login: `evaluate_login` dentro de `attempt_login` + `resolve_failure("LOGIN")` fail-closed + mapear CHALLENGE para o OTP existente.
5. Auditoria dos eventos de challenge (ISSUED/VERIFIED/FAILED/EXPIRED) sem segredos.
6. Limites: max tentativas por challenge + cooldown de reemissão (reemitir = novo challenge, mesmo evaluation).
Com isso, challenge, login-enforcement e auditoria fecham o ciclo sem reconstruir nada do que existe.

---
*Documento gerado a partir da inspeção direta do código-fonte; todos os caminhos/linhas referenciados estavam válidos em 2026-08-23.*
