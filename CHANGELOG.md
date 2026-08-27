# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versionamento SemVer (`MAJOR.MINOR.PATCH`). Datas em YYYY-MM-DD.

## [Unreleased]

### Added
- Configuração externa completa (.env.example, helpers env_*, suporte *_FILE/Docker Secrets, falha explícita sem SECRET_KEY em produção) — FASE 10 B1
- `bootstrap_admin` idempotente para o admin inicial via `BANKIO_ADMIN_*` — FASE 10 B1
- Imagem de distribuição: não-root, Gunicorn + WhiteNoise, `/healthz/` com checagem real do banco — FASE 10 B2
- Compose portátil com interpolação `${VAR}` e overlay dev separado — FASE 10 B2
- Documentação de primeira instalação (README Quick Start, dev vs produção, checklist `check --deploy`) — FASE 10 B3
- Higiene open source: CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, SUPPORT, templates de issue/PR — FASE 10 B4
- Licença GPL-3.0-only escolhida pelo mantenedor; OCI label `org.opencontainers.image.licenses` definida — FASE 10 follow-up

### Changed
- Tutorial de primeiro acesso por papel (Driver.js vendored, estado server-side `TourProgress`) — FASE 9
- Baseline de E2E browser (Playwright, 62 jornadas) — pré-FASE 9
