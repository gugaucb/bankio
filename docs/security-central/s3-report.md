# S3 — feat/security-activity-history — Implementation & Judge Report

## OBJECTIVE
Histórico de atividade de segurança na Central, alimentado pelo AuditLog
existente, com paginação server-side e zero exposição de segredos.

## DESIGN
- Fonte: AuditLog do próprio usuário (actor=u), whitelist fechada de ações:
  LOGIN, LOGIN_FAILED, LOGIN_MFA, LOGOUT, PASSWORD_CHANGED, DEVICE_TRUSTED,
  DEVICE_UNTRUSTED, DEVICE_REVOKED, SESSION_REVOKED, OTHER_SESSIONS_REVOKED +
  prefixo CHALLENGE_*. Qualquer outra ação (financeira etc.) nunca aparece.
- Exibição: rótulo legível + timestamp; eventos FAILED em vermelho. NUNCA
  metadata, OTP, hash, password ou tokens (testado com metadados plantados).
- Paginação server-side via django.core.paginator (10/página); página inválida
  é clampada pelo Paginator.
- Reutiliza AuditLog imutável existente — nenhum evento novo criado nesta branch.

## FILES
- `apps/identity/app_views.py` — query whitelisted + Paginator + labels.
- `templates/dashboard/security.html` — seção Recent Security Activity + pager.

## TESTS
whitelist (12 eventos exibidos; financeiro/alheio/sistema ausentes) · segredos
plantados em metadata não vazam · paginação 25 eventos → 3 páginas, navegação e
clamp · escopo por actor · ordenação newest-first · anônimo redirect.
Regressão: **525 passed**. check/makemigrations limpos.

## JUDGE
[✔] histórico é paginado server-side · [✔] apenas eventos próprios
[✔] nenhum segredo exibido · [✔] AuditLog reutilizado · [✔] regressão verde

JUDGE VERDICT: PASS
