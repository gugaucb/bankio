# Bankio

Banco digital simulado — monolito modular Django sobre um **ledger de partidas dobradas imutável**, com Fraud & Risk Engine determinístico, RBAC completo, auditoria e portal do cliente (Django Templates + HTMX + Tailwind).

> Simulador educacional/demonstração. **Não** é produção-banking real: não há integração com pagamentos externos, PSPs ou redes de cartão.

## Arquitetura (resumo)

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.13 · Django 5.1 |
| Banco | PostgreSQL 16 (psycopg 3) |
| Frontend | Django Templates + HTMX + Tailwind CSS |
| Servidor | Gunicorn + WhiteNoise (estáticos) |
| Testes | pytest + Hypothesis + Playwright (E2E browser) |

Saldo = projeção derivada do ledger (nunca coluna mutável). Toda operação sensível passa pelo gate de risco antes de efeito financeiro irreversível.

## Requisitos

- Docker 24+ com Compose v2 (`docker compose version`)
- ~2 GB RAM livres
- Portas 8000 e 5434 livres no host (configuráveis via `.env`)

## Quick Start (instalação limpa)

```bash
git clone <repo>
cd bankio
cp .env.example .env
# editar .env: troque TODO valor CHANGE_ME
# gere uma SECRET_KEY forte:
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Suba o banco, aplique migrations, crie o admin:

```bash
docker compose up -d db
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py bootstrap_admin   # lê BANKIO_ADMIN_* do .env
docker compose up -d                                           # sobe a aplicação
```

Acesse `http://localhost:${BANKIO_PORT:-8000}` e faça login com o admin criado.

### Dados de demonstração (opcional)

Cria clientes fictícios, contas e cartões com credenciais publicadas — **nunca use fora de ambiente local**:

```bash
docker compose exec web python manage.py seed_demo
```

Credenciais de demo (geradas pelo seed): usuário `admin`, senha `Bankio!2026` (staff); clientes como `aubrey.sabina0` / `Customer!2026`. Login do cliente em `/login/`; manager institucional em `/manager/login/`.

## Desenvolvimento vs Produção

- **Dev/demo local**: `DJANGO_DEBUG=true`, runserver com auto-reload. O Makefile usa o overlay dev:
  - `make up` (build + migrate + seed) · `make verify` (check + migrations-check + pytest) · `make test` · `make shell`
- **Produção**: NÃO é este compose-local. Para deploy sério:
  - `DJANGO_DEBUG=false` + `DJANGO_SECRET_KEY` forte/única (ou `_FILE`);
  - HTTPS atrás de proxy reverso; defina `DJANGO_CSRF_TRUSTED_ORIGINS=https://seu-dominio`, `BANKIO_SECURE_COOKIES=true`;
  - Postgres gerenciado com backups; imagens próprias no registry;
  - rode `python manage.py check --deploy` para validar.

### Checklist de produção

```bash
DJANGO_DEBUG=false python manage.py check --deploy
```

Itens esperados: `DEBUG=False`, `SECRET_KEY` única, `ALLOWED_HOSTS` explícito (nunca `*`), `CSRF_TRUSTED_ORIGINS` com scheme, cookies seguros atrás de HTTPS, headers de proxy (`SECURE_PROXY_SSL_HEADER`) quando aplicável.

## Secrets

Padrão suportado `*_FILE` (Docker Secrets / swarm): aponte ex. `DJANGO_SECRET_KEY_FILE=/run/secrets/django_secret_key` — o valor é lido do arquivo e nunca fica no ambiente. `.env` é apenas para desenvolvimento local e está no `.gitignore`.

## Shutdown / Atualização / Backup

```bash
docker compose down            # para sem remover volumes (dados persistem)
# atualizar:
git pull && docker compose build && docker compose up -d && \
  docker compose run --rm web python manage.py migrate
# backup:
docker compose exec db pg_dump -U bankio bankio > backup.sql
# restore:
cat backup.sql | docker compose exec -T db psql -U bankio bankio
```

## Testes

```bash
make verify          # check + makemigrations --check + pytest completo
make test            # somente pytest
```

## Segurança

Vulnerabilidades: **não abra issue pública** — veja [SECURITY.md](SECURITY.md).

## Contribuição

Veja [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

Bankio é distribuído sob a [GNU GPL v3](LICENSE) — derivados distribuídos devem publicar o código-fonte sob a mesma licença.
