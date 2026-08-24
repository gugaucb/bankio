# R4 — feat/auth-risk-challenge-only — Judge Report

## DESIGN
- Consumo da decisão: `_effective_login_action(evaluation)` em
  identity/services.py — decisão vem EXCLUSIVAMENTE da RiskEvaluation
  armazenada + mode atual via `modes.effective_decision`. Nada do cliente
  influencia (testado: decision/risk_level/effective_decision no POST são
  ignorados).
- CHALLENGE reutiliza a infra OTP existente (generate_otp/verify_otp,
  fluxo pending_otp_user da view de login) — usuário sem MFA habilitado
  recebe o MESMO desafio one-time; nenhum terceiro sistema de challenge.
- BLOCK só existe em ENFORCEMENT: CHALLENGE_ONLY rebaixa REVIEW/BLOCK→
  CHALLENGE (modes.effective_decision, já existente). BLOCK audita
  LOGIN_RISK_DENIED e levanta LoginRiskBlocked → view responde erro
  genérico (sem vazar detalhes de risco), sem sessão e sem pending_otp_user.
- FAIL-SAFE: `resolve_failure("LOGIN")` = FAIL_CONNECTED consumido — engine
  failure fora de SHADOW/DISABLED recusa o login (LoginRiskBlocked); em
  SHADOW prossegue com evidência (comportamento R1 preservado).
- Lockout mantém precedência sobre qualquer decisão de risco.
- Ordem preservada: lockout → credenciais → device → RISCO → OTP/challenge
  → sessão → audit. Sessão nunca criada antes da decisão final.

## FILES
- `apps/identity/services.py` — LoginRiskBlocked, _effective_login_action,
  _deliver_otp, consumo da decisão em attempt_login.
- `apps/identity/views.py` — catch LoginRiskBlocked (erro genérico).
- `tests/test_auth_risk_challenge_only.py` — 13 testes.

## AUTH RISK PROMOTION REPORT (GATE)
| Campo | Valor |
|---|---|
| Stage | CHALLENGE_ONLY implementado e testado; default permanece SHADOW |
| ALLOW / CHALLENGE / REVIEW / BLOCK | LOW→ALLOW · MEDIUM/HIGH→CHALLENGE · CRITICAL: CHALLENGE_ONLY→challenge, ENFORCEMENT→block (testados) |
| Engine errors | FAIL_CLOSED fora de shadow (login recusado + evidência RISK_EVALUATION_ERROR) |
| Intervention rate | controlada pelo mode; shadow = 0 intervenção (regressão verde) |
| Known limitations | entrega OTP ainda via logger demo; REVIEW em enforcement tratado como allow (policy LOGIN não produz REVIEW) |

## TESTES
shadow não interfere (score100) · LOW direto em challenge_only ·
MEDIUM/HIGH→OTP step-up (parametrizado) · CRITICAL rebaixado em
challenge_only · OTP errado falha/correto completa · fluxo MFA inalterado ·
BLOCK só em enforcement · bloqueio não cria sessão/pending_otp e resposta
genérica · fail-closed engine error fora de shadow · shadow tolera engine
error · smuggling de decisão pelo cliente ineficaz · lockout precedence.
Regressão: **568 passed**. check limpo. migrations OK.

## JUDGE
[✔] policy LOGIN aplicada sem hardcode na view · [✔] challenge = MFA/OTP
existente · [✔] step-up seguro p/ sem-MFA (mesma infra) · [✔] BLOCK obedece
mode exatamente · [✔] resolve_failure("LOGIN") consumido, fail-closed,
jamais ALLOW silencioso · [✔] evidência RiskEvaluation/AuditLog ·
[✔] regressão verde

VERDICT: PASS (modo default segue SHADOW; ativação de CHALLENGE_ONLY é
decisão operacional via set_mode auditado)
