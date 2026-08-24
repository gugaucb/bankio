# FASE 6 B7 — feat/notification-preferences — Judge Report

## DESIGN
- `NotificationPreference` (user+category único; row ausente = ON) em
  apps/notifications/models.py + migration 0003.
- Enforcement dentro de `notify()`: kind ∉ MANDATORY e categoria OFF →
  return None (drop silencioso de eventos FUTUROS apenas; linhas existentes
  intactas). Kinds obrigatórios (PASSWORD_CHANGED, MFA_*, NEW_DEVICE,
  USER_BLOCKED/UNBLOCKED, CHALLENGE_*) NUNCA suprimidos.
- `set_category_preference`: update_or_create + audit
  NOTIFICATION_PREFERENCES_CHANGED {category, enabled}; categoria inválida
  rejeitada.
- UI: card Preferences na Central com toggle por categoria (POST →
  app_notifications), redirect, categoria inválida ignorada.

## FILES
- apps/notifications/models.py (+NotificationPreference) · migrations/0003 ·
  services.py (+gate +set_category_preference) · identity/app_views.py
  (+POST handler + pref_rows) · templates/dashboard/notifications.html
  (+card) · tests/test_notification_preferences.py (+8).

## TESTES
default ON sem row · OFF muta só futuros, antigos intactos · re-enable
retoma · mandatory ignora preferência (4 kinds provados) · não-mandatory
security é mutado · mudança auditada + categoria inválida rejeitada ·
constraint única (1 row/categoria) · POST da view: toggle, categoria
inválida ignorada, sem IDOR/mass-assignment entre usuários.

## GATES
pytest **705 passed** · check limpo · migrations OK.

JUDGE: [✔] whitelist enforcement [✔] OFF só afeta futuros [✔] audit
[✔] UI funcional [✔] mass-assignment seguro [✔] regressão verde

JUDGE VERDICT: PASS
