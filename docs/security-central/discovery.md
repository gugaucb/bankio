# SECURITY CENTRAL — DISCOVERY REPORT (Fase 0)

## 1. Identity models (apps/identity/models.py)
- `User(AbstractUser)`: único User; `role`, `phone`, `mfa_enabled` (default False),
  `mfa_secret` (char 12 — hash demo do OTP), `failed_login_count`, `locked_until`.
- `Device`: FK user (related `devices`); `device_id` = sha256(UA + "|" +
  Accept-Language)[:64]; `name` = UA[:120]; `first_seen` auto_now_add;
  `last_seen` auto_now; `trusted` BooleanField(default=False);
  unique_together (user, device_id).

## 2. Device lifecycle (apps/identity/services.py)
- `register_device(user, request)` chamado em `attempt_login()` após senha OK →
  get_or_create por (user, device_id).
- `is_new_device(user, request)` = NÃO existe Device trusted=True com esse hash.
- **INCONSISTÊNCIA**: nenhum fluxo em todo o código define `trusted=True`.
  Consequência: todo device é sempre "novo" para os sinais de fraude.
- Consumidores: apps/fraud/signals.py lê `Device.trusted` (device novo/não-confiável
  é sinal) e conta sharing de device entre users. Correção de semântica (trust
  explícito pelo dono) NÃO altera essas regras — apenas torna o estado
  trusted=True alcançável, sem ativar regras novas automaticamente.

## 3. Sessions
- Django DB-backed sessions padrão (settings: SESSION_COOKIE_AGE=3600,
  SESSION_SAVE_EVERY_REQUEST=True, HTTPONLY, SameSite=Lax).
- Metadados por sessão: NENHUM além do payload Django (_auth_user_id, expiry).
  IP/UA existem apenas no AuditLog (por evento, não por sessão).
- `_kill_sessions(user)` em admin_services.py percorre django_session e deleta as
  do alvo — usado pelo admin block. É a infraestrutura a reutilizar para
  revogação self-service.
- Para uma UI segura faltam: created_at + identificação do device da sessão.
  Estrutura mínima justificada (S2): modelo leve SessionRecord(session_key unique,
  user, created_at, user_agent, device_hash) preenchido no login; exibição mostra
  apenas dados realmente persistidos.

## 4. OTP/MFA (apps/identity/services.py)
- `generate_otp(user)`: código 6 dígitos; `mfa_secret = sha256(code)[:12]`;
  docstring diz "válido 5 minutos" mas **NÃO há timestamp nem checagem temporal**
  — verify_otp aceita indefinidamente até consumo. Bug confirmado; S4 deve
  aplicar expiração real (campo novo otp_generated_at + TTL na verificação).
- `verify_otp`: compara hash; consome limpando mfa_secret (single-use ✓;
  replay falha).
- `attempt_login`: lockout 5 tentativas/15min; se mfa_enabled → gera OTP e
  devolve needs_otp=True; sessão guarda pending_otp_user até verify.
- **mfa_enabled nunca é alterado por nenhum serviço hoje** — self-service é
  terreno novo mas reutiliza OTP existente.

## 5. Login/logout/auditoria
- login_view/otp_verify_view/logout_view (identity/views.py): audit LOGIN,
  LOGIN_MFA, LOGOUT; LOGIN_FAILED com actor no attempt_login.
- AuditLog imutável (save/update e delete bloqueados); grava ip_address, device(UA),
  metadata. PASSWORD_CHANGED auditarado em app_views.security_view (+ shadow risk).

## 6. UI atual
- `/app/security/` (app_views.security_view) + templates/dashboard/security.html:
  apenas troca de senha + últimos logins (LOGIN*). Extends dashboard/shell.html.
  A Central estende esta view/template preservando design (classes card/btn).

## 7. Riscos / decisões
- IDOR: todos os novos endpoints filtram por request.user (owner-or-404).
- Sessão: revogação só da própria; identificar sessão atual por session_key.
- OTP expiração: campo novo no User justificado (única fonte de verdade do OTP
  é o próprio user; criar tabela nova seria segundo mecanismo).
