# S4 — feat/security-mfa-self-service — Implementation & Judge Report

## OBJECTIVE
MFA self-service na Central de Segurança reutilizando o OTP existente, com
expiração temporal REAL corrigida e desativação protegida por reautenticação.

## DESIGN
- **Bug confirmado e corrigido**: generate_otp documentava 5 minutos mas não
  havia timestamp nem checagem. Adicionado `User.otp_generated_at` (migration
  0004); `verify_otp` falha fechado quando `now > otp_generated_at +
  OTP_TTL_MINUTES(5)` — inclusive para segredos legados sem timestamp.
- **Enable**: start → OTP entregue pelo canal simulado (`bankio.challenge`,
  mesmo canal do step-up) + audit MFA_ENABLE_STARTED; confirm → verify_otp
  (single-use, com TTL) → mfa_enabled=True + audit MFA_ENABLED. Sem confirmação
  válida o MFA nunca é ativado.
- **Disable**: exige reautenticação por senha (check_password) no serviço;
  POST autenticado apenas por sessão não desabilita (testado). Audit
  MFA_DISABLED.
- **Brute force**: códigos errados não consomem nem queimam o código correto
  (single-use apenas no acerto); expirado falha fechado; replay rejeitado.
- **Login**: fluxo existente de OTP intocado; agora também entrega pelo canal
  simulado (paridade dev) e herda o TTL real.
- Auditoria sem segredos: código nunca vai para HTML/auditoria (testado).

## FILES
- `apps/identity/models.py` + migration 0004 — otp_generated_at.
- `apps/identity/services.py` — TTL em generate/verify_otp; start_mfa_enable,
  confirm_mfa_enable, disable_mfa, MFAError; entrega do login code via logger.
- `apps/identity/app_views.py` + template — seção MFA (status/enable/disable).

## TESTS
OTP expira após TTL (correto também morre) · segredo legado sem timestamp falha
fechado · replay rejeitado · 10 códigos errados não queimam o correto · enable
UI start→confirm · enable com código errado/expirado nunca ativa · disable sem
senha/wrong password bloqueado (view e serviço), senha correta desabilita · CSRF
403 · status na página sem vazar código · login E2E com MFA via /otp/.
Regressão: **535 passed**. check/makemigrations limpos.

## JUDGE
[✔] User/Device/Session/AuditLog existentes reutilizados · [✔] OTP existente
reutilizado · [✔] MFA possui expiração temporal real (bug corrigido)
[✔] enable exige confirmação de código · [✔] disable exige reautenticação
[✔] nenhum segundo mecanismo criado · [✔] regressão verde · [✔] login/OTP ok

JUDGE VERDICT: PASS
