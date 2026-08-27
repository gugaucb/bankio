# MEMÓRIA — FASE 10: Distribuição pública (GitHub · Docker Hub · Compose)

Data: 2026-08-27 · Tag base: `baseline/pre-distribution` · Estratégia: ORQUESTRADOR → DISCOVERY → branch/parte → TESTES → JUIZ → merge --no-ff.

## Branches

| Branch | Escopo | Veredito |
|---|---|---|
| `feat/distribution-environment-config` (B1) | `config/env_utils.py`, settings externalizado, `.env.example`, `.gitignore`, `bootstrap_admin`, 12 testes | PASS |
| `feat/distribution-docker` (B2) | Dockerfile não-root + gunicorn + whitenoise + `/healthz/`; compose portátil `${VAR}`; overlay dev; `.dockerignore` | PASS |
| `feat/distribution-first-run` (B3) | README Quick Start, secrets, dev-vs-prod, checklist `check --deploy` | PASS |
| `feat/distribution-github-community` (B4) | SECURITY/CONTRIBUTING/CoC/SUPPORT/CHANGELOG/templates + LICENSE_DECISION.md | PASS |
| `feat/distribution-ci` (B5) | `ci.yml` (check, migrations-check, pytest, check --deploy), dependabot (pip/actions/docker) | PASS |
| `feat/distribution-dockerhub-release` (B6) | `dockerhub-release.yml`: tags SemVer→Hub, multi-arch amd64/arm64, OCI labels, SBOM/provenance | PASS |
| `test/distribution-clean-install` (B7) | Simulação de usuário externo em `/tmp`, variação de config, secret scan, regressão completa | PASS |

## Decisões-chave

1. **Servidor é autoridade da config**: helpers puros (`env_bool/env_int/env_list/secret_or_file`); `DEBUG=false` sem `DJANGO_SECRET_KEY` **falha ao bootar**; chave dev conhecida é rejeitada em produção. Nunca host-based detection.
2. ***_FILE** para qualquer sensível (Docker Secrets): `DJANGO_SECRET_KEY_FILE`, `POSTGRES_PASSWORD_FILE`, `BANKIO_ADMIN_PASSWORD_FILE`.
3. **Admin inicial**: comando explícito idempotente (`bootstrap_admin`) — nunca reset, nunca no startup, senha só hash, nada de senha em log.
4. **Imagem**: python:3.13-slim, usuário `bankio` não-root, gunicorn 3 workers, WhiteNoise servindo `staticfiles` (coletado no build). Secret do collectstatic via `--mount=type=secret` com fallback placeholder não-secreto.
5. **Compose**: base portátil (`${VAR:-default}`); conveniências dev isoladas em `docker-compose.dev.yml` (bind-mount + runserver) — `make up` usa as duas. `env_file: .env required:false` injeta variáveis no container (inclui BANKIO_ADMIN_* para o bootstrap).
6. **Healthcheck real**: `/healthz/` valida process + `SELECT 1` no Postgres; db usa `pg_isready` com user/db interpolados; web depende de `service_healthy` (sem sleeps).
7. **Licença**: NÃO escolhida pelo mantenedor → `LICENSE_DECISION.md` com MIT/Apache-2.0/GPL/AGPL/BSD e impacto; `org.opencontainers.image.licenses` deliberadamente omitido até decisão.
8. **CI mínima de permissões** (`contents: read`), secrets apenas via GitHub Secrets (`DOCKERHUB_USERNAME/TOKEN`); `latest` somente em tag `v*`.

## Evidências do B7 (gate principal)

- Instalação limpa em `/tmp/bankio-cleaninstall` a partir de `git archive main`: `.env` novo (outra porta 8010, outro DB/user/senha, SECRET_KEY gerada) → migrate → bootstrap_admin (`cleanadmin` criado) → up → `/healthz/ ok` → login browser OK (zero erros JS).
- **Segunda instalação** com POSTGRES_DB/USER/PASSWORD, BANKIO_PORT(8011), ALLOWED_HOSTS diferentes → funciona ponta-a-ponta ⇒ sem hardcodes ocultos.
- `docker history` / `image inspect`: nenhum segredo embutido (0 matches; env vazio de credenciais).
- `git grep` sem credenciais reais versionadas; únicos valores = defaults de dev documentados (`CHANGE_ME` x4 no `.env.example`).
- Regressão final: **798 passed** (container) + **62 passed** browser E2E. BASELINE_TESTS=774 (pré-FASE 9) ≤ FINAL_TESTS=798.

## Pendências intencionais (ação do mantenedor)

- ~~Escolher licença~~ → **RESOLVIDO: GPL-3.0-only** (decisão do mantenedor; LICENSE na raiz, OCI label `org.opencontainers.image.licenses=GPL-3.0-only`, README/CHANGELOG atualizados, LICENSE_DECISION.md removido). Merge `feat/license-gpl3`.
- Criar secrets `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` no GitHub (consumidos via `${{ secrets.* }}` no workflow); ativar Dependabot alerts/secret scanning/push protection na UI do GitHub (não configurável via repo files).
- Publicar primeira tag `v1.0.0` para disparar o pipeline Docker Hub e repetir clean-install usando a imagem publicada.
- Remote `origin` apontando para https://github.com/gugaucb/bankio (1º push pendente).
