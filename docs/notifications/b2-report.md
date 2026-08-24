# FASE 6 B2 — feat/notification-center-ui — Judge Report

## DESIGN
- `/app/notifications/`: central customer-facing reutilizando shell.html,
  badge existente (agora link para a central), Paginator server-side 20/pág.
- Ordenação determinística herdada do model (-created_at,-id).
- Filtros GET: state=all/unread/read + category (choices validadas;
  valor inválido degrada para sem filtro). Paginação preserva filtros.
- Mark read: POST-only, ownership via filter(recipient=user)→404
  indistinguível, CSRF, idempotente com read_at estável. Mark-all POST bulk.
- Sem |safe; autoescape cobre title/body. Badge reduz após leitura
  (context processor existente reutilizado).

## FILES
- apps/identity/app_views.py (+3 views) · urls.py (+3 rotas) ·
  templates/dashboard/notifications.html (novo) · shell.html (badge→link) ·
  tests/test_notification_center.py (+12).

## TESTES
empty state · listagem+badge count · paginação 25→2 páginas · ordering ·
mark read POST/idempotente/GET-não-destrutivo · mark all · filtros
categoria/estado/inválido-seguro · IDOR read → 404 e não marca · XSS
escapado · badge diminui após leitura · queries ≤ 12 por página ·
anônimo → login.

## GATES
pytest **670 passed** · check limpo · migrations OK.

JUDGE: [✔] badge reutilizado [✔] paginada [✔] IDOR 404 [✔] ações POST
[✔] CSRF [✔] XSS [✔] sem N+1 [✔] regressão verde

JUDGE VERDICT: PASS
