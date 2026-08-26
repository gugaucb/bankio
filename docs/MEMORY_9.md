# MEMÓRIA — FASE 9: Tutorial de primeiro acesso

Data: 2026-08-25 · Branch strategy: tag `baseline/pre-tutorial` → B1 → B2 → merge --no-ff em main.

## Branches

| Branch | Escopo | Resultado |
|---|---|---|
| `feat/first-access-tour-core` (B1) | Modelo `TourProgress`, `apps/identity/tour.py`, views/urls do tour, `_tour.html`, testes de domínio | PASS |
| `feat/first-access-tour-customer` (B2) | Steps reais por papel (customer 10 steps / staff 2), hooks `data-tour` nos templates, variante staff no dashboard manager, suíte browser E2E | PASS |

## Decisões

1. **Biblioteca: Driver.js 1.3.1 vendored** (`static/js/vendor/driver.iife.js` + `driver.css`). Validada contra Django Templates/HTMX/Tailwind/CSP/mobile/teclado. Sem CDN/@latest, sem framework frontend. Nota: o build IIFE expõe `window.driver.js.driver(...)` (namespace), não `window.driver.js(...)`.
   Fallback considerado e descartado: Shepherd.js (mais pesado, exige popper).
2. **Servidor é a autoridade**: modelo `TourProgress` (OneToOne User; `tour_version`, `completed_at`, `skipped_at`). Nada de localStorage/cookie-only. Sem linha ⇒ auto-start no próximo dashboard.
3. **Tour só após autenticação completa**: apenas os dashboards (customer e manager) renderizam `_tour.html` — nunca login/OTP/challenge/erro.
4. **Replay one-shot**: flag de sessão server-side (`tour_replay`) consumida no render; link "Ver tutorial novamente" em Configurações → Ajuda.
5. **RBAC**: `customer_steps()` referencia somente hooks `nav-*`; `staff_steps()` mostra apenas visão geral. Nenhum step cita telas de fraude/compliance/admin para clientes (verificado por teste).
6. **Hooks `data-tour` são UI-only**: nunca usados para autorização.
7. **Graceful degradation**: guard `window.driver.js.driver` no load — se a lib falhar, o app funciona intacto (critério do juiz #10).
8. **Semântica Concluir vs Pular**: sair no último step = complete; qualquer outra saída (Escape/overlay) = skip. Detectado via MutationObserver que marca presença do botão "Concluir" (o driver reseta estado durante destroy, então `getActiveIndex()` não é confiável dentro de `onDestroyed`).

## Bugs encontrados e corrigidos durante E2E

- JSON dos steps era escapado pelo template (`&quot;`) → `{{ tour_steps|safe }}` (conteúdo 100% server-generated).
- Chamada errada da factory (`window.driver.js({...})` → TypeError) → `window.driver.js.driver({...})`.
- Corrida complete/skip: dois POSTs disputavam a última escrita → decisão única dentro de `onDestroyed`.

## Cobertura de testes

- Domínio: `tests/test_first_access_tour.py` (11) — estado server-side, endpoints POST-only, 404 outcome inválido, replay one-shot, RBAC dos steps.
- Browser: `e2e_browser/test_browser_tour.py` (7) — primeiro acesso+skip persiste e não reinicia; fluxo completo persiste; replay manual único; navegação Próximo/Voltar/foco; steps do cliente sem telas staff; staff com steps role-scoped; mobile 390×844 dentro do viewport.
- Gates finais: container `check + makemigrations --check + pytest` = **786 passed**; suite browser completa = **62 passed**.
