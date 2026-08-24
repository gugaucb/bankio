# R1 — feat/auth-risk-shadow-integration — Judge Report

## Stage
SHADOW (nenhuma decisão de risco interfere no login).

## DESIGN
- Ordem explícita em `attempt_login()`: lockout → credenciais → device
  registration → **risk evaluation** → OTP → sessão → audit.
- Sessão só é criada após a decisão; avaliação fora de bloco atômico
  (evidência sobrevive, INV 9); usuário inexistente nunca avaliado
  (anti-enumeração); lockout intacto e risco jamais o contorna.
- Engine error: snapshot FAILED persistido pelo engine + audit
  RISK_EVALUATION_ERROR via failsafe.record_failure("LOGIN"); em SHADOW o
  login prossegue (modo observacional), decisão de bloqueio virá só em
  CHALLENGE_ONLY/enforcement.

## FILES
- `apps/identity/services.py` — _login_risk_evaluation + wiring.
- `tests/test_auth_risk_shadow.py` — 7 testes.

## PROMOTION DATA (shadow)
Evaluated logins: produzidos por login real (operation_type=LOGIN).
ALLOW/CHALLENGE/REVIEW/BLOCK: registrados, nunca aplicados.
Engine errors: auditados, não interferem.

## TESTS
login real cria RiskEvaluation SHADOW · usuário inexistente sem avaliação ·
senha errada → LOGIN_FAILED sem avaliação · lockout 5/15 inalterado e não
contornado · device + auditoria preservados · engine exception → FAILED +
audit, login segue em shadow · BLOCK crítico registrado mas nunca aplicado.
Regressão: **542 passed**. check/makemigrations limpos.

## JUDGE
[✔] password validation/lockout/OTP/device/audit preservados
[✔] nenhuma decisão bloqueia em SHADOW · [✔] RiskEvaluation real produzida
[✔] evidência sobrevive a rollback · [✔] anti-enumeração mantida
[✔] regressão verde

VERDICT: PROMOTE (para sinais/ATO — ainda SHADOW)
JUDGE VERDICT: PASS
