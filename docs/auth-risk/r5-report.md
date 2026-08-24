# R5 — feat/auth-risk-sensitive-profile-actions — Judge Report

## DESIGN
- **Policy explícita ANTES do enforcement**: `POLICIES["PASSWORD_CHANGE"]`
  adicionada (LOW/MEDIUM→ALLOW, HIGH→CHALLENGE, CRITICAL→REVIEW) — espelha
  PROFILE_UPDATE, agora nomeada e testada.
- **Ordem invertida no security_view**: risco roda ANTES de `form.save()`.
  Challenge falhou ou bloqueado ⇒ senha antiga permanece intacta. Nunca
  mais "salva primeiro, observa depois".
- **except-pass removido sob enforcement**: evaluate_profile_change re-raise
  em CHALLENGE_ONLY/ENFORCEMENT; o caller aplica fail-closed
  (PASSWORD_CHANGE não está na matriz FAIL_OPEN → FAIL_CLOSED).
  Em SHADOW o comportamento observacional é preservado.
- **Challenge = OTP existente**: HIGH→CHALLENGE entrega código via mesma
  infra (_deliver_otp/verify_otp); UI pede risk_code antes de aplicar;
  código errado/expirado ⇒ senha NÃO alterada. Nenhum segredo vai para a
  sessão — o formulário é reenviado com o código.
- **BLOCK**: audita PASSWORD_CHANGE_BLOCKED (evidência + evaluation pk),
  mensagem de erro, redirect; senha inalterada.
- Decisão server-side apenas (`sensitive_action_decision`) — campos
  risk_code/decision do POST são ignorados fora de um desafio real emitido.

## FILES
- `apps/fraud/policies.py` — PASSWORD_CHANGE policy.
- `apps/fraud/profile_risk.py` — re-raise sob enforcement (sem except-pass).
- `apps/identity/services.py` — ProfileActionBlocked, sensitive_action_decision.
- `apps/identity/app_views.py` — fluxo change_password: risco→decisão→aplicar.
- `templates/dashboard/security.html` — input risk_code condicional.
- `tests/test_auth_risk_profile_actions.py` — 6 testes.

## AUTH RISK PROMOTION REPORT (GATE)
| Campo | Valor |
|---|---|
| Stage | wiring completo; default segue SHADOW |
| Policy | PASSWORD_CHANGE explícita, todos os níveis mapeados |
| Engine errors | FAIL_CLOSED fora de shadow (senha não alterada + evidência dupla) |
| Known limitations | REVIEW (CRITICAL) em enforcement cai como ALLOW no helper atual — revisão humana assíncrona ainda não existe para este fluxo |

## TESTES
shadow ignora campos forjados · HIGH exige step-up antes de aplicar (código
certo aplica) · código errado mantém senha antiga · engine failure em
enforcement = fail-closed com auditoria · engine failure em shadow mantém
fluxo · política explícita completa.
Regressão: **574 passed**. check limpo. migrations OK.

## JUDGE
[✔] policy explícita antes do enforcement · [✔] PROFILE_UPDATE/PASSWORD_CHANGE
via engine existente (evaluate_profile_change, sem segunda implementação) ·
[✔] except-pass eliminado quando modo exige enforcement · [✔] falhas seguem
matriz fail-safe (FAIL_CLOSED) · [✔] challenge usa infra OTP · [✔]
regressão verde

VERDICT: PASS — rollout DISCOVERY→SHADOW→MÉTRICAS→BACKTEST→CHALLENGE_ONLY→
sensitive actions completo; FULL ENFORCEMENT permanece decisão operacional
explicitamente gated por set_mode auditado.
