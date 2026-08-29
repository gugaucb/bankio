# MEMORY 11 — Ajustes funcionais (7 partes) — 2026-08-28

Branches merged com JUDGE PASS e regressão pós-merge verde (base v1.1.1).

## Branches & vereditos
| # | Branch | Conteúdo | Veredito |
|---|--------|----------|----------|
| 1 | feat/manager-customer-full-details | Customer 360 com cards "Profile Details" + "KYC" (todos os campos de negócio); segredos nunca renderizados (testado) | PASS |
| 2 | fix/manager-card-requests-sidebar | card_requests.html usava `{% block content %}` (matava o sidebar do base); corrigido para `{% block page %}` | PASS |
| 3 | feat/card-number-visibility-toggle | **PULADO** por decisão do mantenedor: backend nunca armazena PAN (só last4); não criar storage inseguro p/ UI | n/a |
| 4 | feat/account-funding | Funding via ledger: `fund_account()` (services/accounts), journal balanceado FUND-*, idempotência LedgerIdempotencyRecord, audit FUNDING_EXECUTED, notificação DEPOSIT, UI /manage/funding/ + sidebar | PASS |
| 5 | feat/admin-manager-management | Gap único: `create_user(role=MANAGER)` não criava ManagerProfile → criado (com branch opcional); campo branch no form; link "Managers" no sidebar; block/unblock/sessões reutilizados | PASS |
| 6 | fix/manager-restrictions-screen | Diagnóstico: empty state legítimo (0 registros) + gap real de isolamento → listagem agora scoped por visible_customers; detalhes enriquecidos (requested/approved_by, datas); AML/LEGAL sem botão Lift e lift via POST → 403 | PASS |
| 7 | feat/totp-mfa-enrollment | TOTP RFC 6238 (pyotp): enrollment com QR SVG local (qrcode lib, sem serviço externo), secret Fernet(SHA256(SECRET_KEY)) nos campos `totp_secret_enc`/`totp_last_step` (migration 0006); login exige TOTP; anti-replay por timestep (record_step só no login); brute-force guard 5 tentativas em /otp/; disable exige senha+TOTP; audit MFA_VERIFICATION_FAILED sem secret | PASS |
| F | test/adjustments-regression | Suite browser completa 73/73 + pytest 838/838 + check/makemigrations ok | PASS |

## Decisões
- **PAN nunca persistido** preservado — Ajuste 3 pulado (opção do mantenedor).
- **Funding**: conta sistema `9100-SYSTEM-FUNDING` (ASSET) debitada, liability do cliente creditada; saldo continua derivado do ledger; statement mostra automaticamente.
- **Gerentes**: reutilizado painel `/manage/users/` (NADA recriado); actions auditadas mantidas (ADMIN_USER_CREATED/BLOCKED/UNBLOCKED = equivalentes a ADMIN_MANAGER_*).
- **TOTP**: pyotp+qrcode adicionados ao requirements; anti-replay não consome timestep em enrollment/disable (evita lockout de 30s); apenas verificação de login grava `totp_last_step`.
- **UI MFA antiga (email-OTP enable) aposentada no template**; verify_otp mantido para challenge de risco e legacy.

## Aprendizados
- DEBUG=False ⇒ cached template loader: **reiniciar container web** após editar templates (2× perdido).
- redirect incondicional no fim do bloco POST da security_view engolia renders intermediários (QR) → agora `if totp_data is None: redirect`.
- mesmo código TOTP no mesmo timestep: enrollment/disable NÃO consomem timestep; login consome.
- Tour (Driver.js) intercepta cliques em browser E2E de usuários novos → helper `_skip_tour` (Escape).
- Playwright: página extra precisa de **browser.new_context()** (nova page no mesmo contexto compartilha cookies/sessão).

## Pós-fase: feat/admin-manager-portal-access (2026-08-28) — PASS
- /manager/login/ (+OTP) aceita ADMIN/superuser: ADMIN → /manage/users/ (audit ADMIN_LOGIN[_MFA]), MANAGER → /manage/; cliente segue 403.
- /manage/ com ADMIN sem ManagerProfile → redirect ao painel de usuários (não 403).
- Sidebar contextual por role: manager vê só links managerops; admin só Users/Managers.
- Bônus corrigido pelo juiz: TOTP agora aceito no manager OTP view (record_step=True, anti-replay preservado).
- Testes: 6 unit (test_admin_portal_access.py) + 2 browser; regressão 844+75 verdes.

## Decisão do mantenedor (2026-08-28)
- **Multi-perfil (roles M2M): limitação documentada** — User.role permanece CharField único (1 papel/usuário). Mudança arquitetural adiada.

## Estado final
main = 69288bb (merge admin-portal-access); 844 container + 75 browser E2E verdes; hub-image/publicação não solicitada nesta fase.
