# FASE 6 B6 — feat/notification-security-events — Judge Report

## DESIGN
- Pontos de domínio reais (não inventados):
  - NEW_DEVICE → `register_device` somente quando `created=True` (linha nova
    de Device) — corrige a armadilha "todo dispositivo é novo": dispositivos
    confiáveis reconectando NUNCA notificam (provado por teste com trusted).
  - MFA_ENABLED/MFA_DISABLED → `confirm_mfa_enable` / `disable_mfa`
    (após save; disable exige reautenticação — falha não notifica).
  - USER_BLOCKED/USER_UNBLOCKED → `block_user`/`unblock_user` via
    `transaction.on_commit` (estado só é anunciado após commit); motivo do
    admin NUNCA vaza no body/metadata da notificação do alvo.
  - PASSWORD_CHANGED → ponto de auditoria real em app_security view.
- Dedup: NEW_DEVICE por hash de dispositivo; MFA/BLOCKED por usuário;
  PASSWORD_CHANGED único (uuid) — cada troca notifica.
- notify() nunca propaga falha; categoria SECURITY.

## FILES
- apps/identity/services.py (+3 hooks) · apps/identity/admin_services.py
  (+2 hooks on_commit + helper) · apps/identity/app_views.py (+1 hook) ·
  tests/test_notification_security.py (+6).

## TESTES
whitelist cobre os 6 kinds obrigatórios · NEW_DEVICE 1×/dispositivo e
trusted-relogin zero · MFA enabled+disabled notificam, senha errada não ·
PASSWORD_CHANGED via fluxo real da view · block/unblock exatamente 1× cada,
reason do admin ausente do texto do alvo.

## GATES
pytest **697 passed** · check limpo · migrations OK.

JUDGE: [✔] pontos reais [✔] semântica Device correta [✔] on_commit p/
estado de conta [✔] privacidade (sem reason leak) [✔] regressão verde

JUDGE VERDICT: PASS
