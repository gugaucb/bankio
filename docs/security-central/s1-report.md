# S1 — feat/security-devices — Implementation & Judge Report

## OBJECTIVE
Seção DISPOSITIVOS na Central de Segurança usando o Device existente, sem
recriar modelo e sem alterar regras de fraude.

## DESIGN
- Listagem mostra SOMENTE dados reais do modelo: name, first_seen, last_seen,
  trusted e marcador "This device" (comparação device_id == sha256(UA|lang) da
  request atual — mesmo algoritmo de `_device_hash`).
- Ações: trust / untrust / revoke. Todas owner-scoped (`filter(pk, user=user)` →
  DEVICE_NOT_FOUND indistinguível de inexistente = IDOR-safe); ids manipulados
  são no-ops silenciosos com redirect.
- **Correção de semântica**: nenhum fluxo definia trusted=True; agora o DONO
  marca explicitamente ("Trust device"). Nenhuma regra de risco nova ativada:
  `is_new_device()` e apps/fraud/signals continuam lendo o mesmo campo com a
  mesma lógica — apenas o estado passa a ser alcançável por escolha do usuário.
- Auditoria: DEVICE_TRUSTED / DEVICE_UNTRUSTED / DEVICE_REVOKED — metadata
  contém nome truncado e só os 12 primeiros chars do hash (nada sensível).
- CSRF obrigatório (middleware), POST-only para mutações.

## FILES
- `apps/identity/services.py` — current_device_hash, DeviceError,
  trust_device/untrust_device/revoke_device.
- `apps/identity/app_views.py` — security_view: bloco POST exclusivo (elif)
  para devices; contexto devices[].
- `templates/dashboard/security.html` — seção Devices preservando design.
- `tests/test_security_devices.py` — 9 testes.

## TESTS
listagem só com campos reais + marcador único · não lista devices de outros ·
trust/untrust/revoke auditados · IDOR em 3 ações não altera nem apaga device
alheio · ids vazios/lixo/inexistentes = no-op · CSRF 403 · anônimo redirect ·
trusted alimenta is_new_device (semântica corrigida, sinal de fraude intacto).
Regressão: **512 passed**. check/makemigrations limpos.

## JUDGE
[✔] Device existente reutilizado · [✔] usuários só veem próprios devices
[✔] trust explícito, revogável · [✔] auditoria DEVICE_* sem segredos
[✔] nenhuma regra de fraude alterada · [✔] regressão verde

JUDGE VERDICT: PASS
