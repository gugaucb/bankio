# R2 — feat/auth-risk-signals — Judge Report

## Stage
SHADOW (sinais e ATO agora alimentam o engine no login real; nada bloqueia).

## DESIGN
- Sinais auth existentes (IP_DIFFERS_FROM_LAST_LOGIN, LOGIN_VELOCITY_15MIN,
  NEW_DEVICE, FAILED_LOGIN_COUNT_24H) já são coletados pelo pipeline do login
  real via REGISTRY — verificados com valores reais em RiskSignal.
- **ATO**: ponte `ATO_CORRELATION_POINTS` registra correlate_account_takeover()
  como signal do engine; influência definida como signal → regra (RiskRule em
  banco, ex.: points ge 30) → score → policy LOGIN. Nada hardcodado em view.
- **MFA_FAILURE_COUNT_24H** agora tem produtor real: falha de OTP no login
  grava LOGIN_MFA(metadata otp_failure=true); signal conta só falhas.
- **Bug real corrigido**: templates/auth/otp.html não existia — qualquer OTP
  errado derrubava o fluxo com 500. Template criado.
- Device: NEW_DEVICE = não-conhecido OU trusted=False; trusted=False é "ainda
  não confiável", não veredito de ataque (testado: trust explícito muda sinal).

## FILES
- `apps/fraud/ato.py` — bridge de signal + registro.
- `apps/fraud/apps.py` — importa ato no ready.
- `apps/fraud/signals_auth.py` — MFA failure filtra metadata otp_failure.
- `apps/identity/views.py` — produtor de falha OTP.
- `templates/auth/otp.html` — novo (corrige 500).
- `tests/test_auth_risk_signals.py` — 7 testes.

## PROMOTION DATA (shadow)
Sinais observados em logins reais: IP change True/None-baseline, velocity ≥2,
NEW_DEVICE True→False pós-trust, MFA failures contadas, ATO points 0/30+.

## TESTS
ip change · velocity · mfa failure producer+signal · new device/trusted
semantics · ATO registrado e pontuado · regra ATO→score→decisão sem hardcode
(shadow nunca bloqueia) · signal quebrado isolado não derruba login.
Regressão: **549 passed**. check limpo.

## JUDGE
[✔] sinais existentes conectados ao login real · [✔] ATO sem segunda
implementação · [✔] correlação entra como signal/rule/score · [✔] sem BLOCK
hardcodado · [✔] trusted=False ≠ ataque (testado) · [✔] regressão verde

VERDICT: PROMOTE (para métricas/backtest — ainda SHADOW)
JUDGE VERDICT: PASS
