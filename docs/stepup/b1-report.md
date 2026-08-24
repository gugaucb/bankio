# BRANCH 1 — feat/step-up-challenge-ui — Implementation & Judge Report

## OBJECTIVE
Interface customer-facing para o step-up challenge existente + entrega out-of-band do código.

## FILES
- `apps/fraud/challenge_delivery.py` (novo) — `issue_and_deliver()`: reutiliza `issue_challenge()` intocado; entrega simulada via logger `bankio.challenge` (stand-in SMS/e-mail); Notification in-app SEM o código; audit `CHALLENGE_ISSUED` só com identificadores.
- `apps/fraud/challenge_views.py` (novo) — GET apresenta / POST valida server-side via `verify_challenge()`; owner-or-404 (IDOR); fatos whitelisted (`FACT_KEYS`); expiração preguiçosa via `_expire_if_due()` reaproveitada.
- `apps/fraud/urls.py` — rota `security/challenge/<int:challenge_id>/` (namespace `fraud:`).
- `templates/security/challenge.html` (novo) — estende `dashboard/shell.html`; campo código, contexto mínimo (tipo/valor/moeda), estados expirado/usado/verificado, erro inválido, CSRF.
- `apps/transfers/services.py` — emissão agora via `issue_and_deliver`; `TransferError.challenge_id` anexado (fim do parse de string).

## SECURITY CONTRACT VERIFIED
- Código/hash/material_hash jamais no HTML, query, AuditLog ou DB plaintext (testes de leak).
- GET nunca valida; POST-only validation com CSRF (teste enforce_csrf_checks → 403, estado intacto).
- IDOR: challenge de outro usuário → 404 indistinguível de inexistente.
- Fatos não-whitelisted descartados; tampering → MATERIAL_CHANGED + EXPIRED (testado).
- Replay/double-submit pós-VERIFIED falha (testado na UI e no serviço).

## TESTS
16 novos em `tests/test_step_up_ui.py`. Regressão completa: **476 passed** (baseline 460). `manage.py check` e `makemigrations --check` limpos.

## JUDGE
[✔] backend existente reutilizado (challenge.py/models.py intocados)
[✔] nenhuma segunda implementação de challenge
[✔] código nunca aparece no client
[✔] CSRF funcionando · [✔] IDOR bloqueado
[✔] auditoria sem segredos · [✔] entrega fora de canais proibidos
[✔] regressão verde

JUDGE VERDICT: PASS
