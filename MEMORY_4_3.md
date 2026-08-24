# MEMORY_4_3 — Integração Fraud & Risk Engine ↔ Autenticação

Missão: conectar evaluate_login / signals_auth / ATO / fail-safe LOGIN ao fluxo
real de autenticação, com rollout em estágios (cada um em branch própria,
merge --no-ff só com PASS do juiz). FULL ENFORCEMENT NÃO será ativado.

## Rollout obrigatório
SHADOW → BACKTEST → CHALLENGE_ONLY → LIMITED ENFORCEMENT → FULL ENFORCEMENT

## Componentes existentes (NÃO reimplementar)
- `apps/fraud/auth_risk.evaluate_login(user, request, device_id, ip)` — engine pipeline LOGIN
- `apps/fraud/signals_auth.py` — IP_DIFFERS_FROM_LAST_LOGIN, LOGIN_VELOCITY_15MIN, MFA_FAILURE_COUNT_24H (@register; importados em apps.py)
- `apps/fraud/signals.py` — NEW_DEVICE (lê Device.trusted), FAILED_LOGIN_COUNT_24H, DEVICE_IP_FAILED_LOGINS_24H
- `apps/fraud/ato.correlate_account_takeover(user)` → {factor_count, factors, ato_points, explanation}
- `apps/fraud/failsafe.py` — resolve_failure("LOGIN") = FAIL_CLOSED; record_failure audita RISK_EVALUATION_ERROR
- `POLICIES["LOGIN"]` LOW→ALLOW MEDIUM→CHALLENGE HIGH→CHALLENGE CRITICAL→BLOCK; `modes.effective_decision`
- `RiskEvaluation`, FRAUD_MODE (default SHADOW), OTP login, AuditLog

## Ordem definida do login (R1)
1. lockout check (comportamento atual intacto)
2. credential validation (authenticate) + reset contadores
3. device registration (register_device — já existia)
4. **risk evaluation (evaluate_login)** — FORA de transação atômica: evidência
   sobrevive a qualquer rollback posterior (INV 9); engine grava FAILED +
   re-raise em erro interno
5. decisão por modo: SHADOW nunca interfere; CHALLENGE/BLOCK só em modos futuros
6. OTP se mfa_enabled / step-up (branch R4)
7. session creation (auth.login) — sempre APÓS a decisão final
8. audit (LOGIN / LOGIN_MFA)

Anti-enumeration: avaliação só roda para usuário EXISTENTE com senha válida
(usuário inexistente retorna antes, como hoje).

## Progresso

| Branch | Escopo | Status | Testes | Juiz |
|---|---|---|---|---|
| feat/auth-risk-shadow-integration | evaluate_login no login real, SHADOW | concluído (merge --no-ff) | 542 passed | PASS / PROMOTE |
| feat/auth-risk-signals | sinais auth + ATO no login real | concluído (merge --no-ff) | 549 passed | PASS / PROMOTE |
| feat/auth-risk-backtesting | métricas + backtest LOGIN + promotion report | concluído | 555 passed | PASS / PROMOTE (para CHALLENGE_ONLY) |
| feat/auth-risk-challenge-only | policy LOGIN aplicada, step-up OTP, FAIL_CLOSED | concluído (default SHADOW) | 568 passed | PASS |
| feat/auth-risk-sensitive-profile-actions | PASSWORD_CHANGE/PROFILE_UPDATE policies + enforcement | pendente | — | — |

## Decisões
- R1: wiring em `attempt_login()` após senha válida; SHADOW não bloqueia nada;
  engine error em SHADOW não derruba o login (evidência registrada).
- R2: ATO entra como SINAL (`ATO_CORRELATION_POINTS`, valor = ato_points),
  reutilizando correlate_account_takeover — sem segunda implementação e sem
  BLOCK hardcodado; score/decisão seguem rules→scoring→policy.
  MFA_FAILURE_COUNT_24H agora tem produtor real: falha de OTP grava
  LOGIN_MFA com metadata otp_failure=true.
- R3: backtest usa RiskEvaluation reais (operação LOGIN) + eventos AuditLog;
  intervention/challenge/block rate medidos; SEM precision/recall inventada
  (sem ground truth). Candidate ruleset default do backtest = regras enabled
  no banco; gate = enforcement_gate reaproveitado (block ≤5%, review ≤20%).
- R4: CHALLENGE_ONLY reutiliza OTP/MFA existente (pending_otp_user na sessão);
  usuário sem MFA habilitado recebe desafio OTP one-time igual ao fluxo de
  login MFA (mesma infraestrutura, sem terceiro sistema). Engine failure →
  FAIL_CLOSED (login recusado + evidência), jamais ALLOW silencioso.
- R5: policy explícita PASSWORD_CHANGE adicionada em POLICIES (LOW/MEDIUM→ALLOW,
  HIGH→CHALLENGE, CRITICAL→REVIEW); sob enforcement, falha de challenge em
  mudança de senha bloqueia a ação (senha NÃO alterada) e falha do engine segue
  matriz failsafe; try/except pass removido quando modo exige enforcement.

## Limitações conhecidas
- Sem geo-IP (COUNTRY_CHANGE explicitamente não fabricado, D-F04).
- IP em testes via REMOTE_ADDR do test client ("127.0.0.1") — sinal de troca de
  IP exercitado unitariamente.
- FULL ENFORCEMENT permanece NÃO ativado (fora de escopo desta missão).
