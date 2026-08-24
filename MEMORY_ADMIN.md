# MEMORY_ADMIN.md — Painel Administrativo de Usuários

Missão: Painel Admin de Usuários (orquestrador + implementador + juiz).
Estratégia: 4 branches → merge --no-ff só com JUDGE PASS.

| # | Branch | Escopo | Status | Judge | Merged |
|---|---|---|---|---|---|
| B1 | feat/admin-user-management-core | domínio/serviços + testes | DONE | PASS | SIM |
| B2 | feat/admin-user-management-ui | views/templates busca/filtros/paginação/bloqueio | DONE | PASS | SIM |
| B3 | feat/admin-dashboard | dashboard admin + auditoria recente | DONE | PASS | SIM |
| B4 | test/admin-user-management-regression | E2E/adversarial/regressão/hardening | DONE | PASS | SIM |

## Decision Log
- D-A01: Role.ADMIN reaproveitado como papel de admin (spec permite; sem segundo modelo de usuário).
- D-A02: Namespace /manage/users/* (não /admin/) para evitar colisão com Django admin.
- D-A03: Bloqueio = is_active=False (ModelBackend já rejeita inativos) + invalidação de sessões DB por scan de _auth_user_id.
- D-A04: Proteções (self-block, last-admin, motivo obrigatório) na camada de serviços — qualquer caller futuro herda a segurança.
- D-A05: E2E via Django test client, convenção existente do repo (tests/test_e2e_journeys.py); sem navegador live.
- D-A06: Dashboard mostra target como resource_id do AuditLog (sem fabricar dados); atalho "Auditoria" ancora na seção local (não há console de auditoria separado).

## Session notes
- Baseline: 405 testes verdes; main em e5faad0.
- Final: 460 testes verdes após B4; check + makemigrations --check limpos pós-cada merge.
- Aceite final: FUNCIONALIDADE + SEGURANÇA + AUDITORIA + TESTES + REGRESSÃO + JUDGE PASS ✔
