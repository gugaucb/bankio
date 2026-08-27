# CONTRIBUTING.md

Obrigado por considerar contribuir com o Bankio.

## Fluxo de trabalho

1. Fork ou branch a partir de `main`: `feat/<tema>` / `fix/<bug>` / `test/<alcance>`.
2. Uma branch por parte/escopo. Commits pequenos e descritivos (imperativo: `feat(transfers): ...`).
3. Pull Request para `main`. Merges são `--no-ff` e somente com suíte verde.

## Setup local

Siga o Quick Start do [README](README.md) (`cp .env.example .env`, compose up, migrate, bootstrap_admin).

## Antes de abrir o PR

```bash
make verify        # check + makemigrations --check + pytest completo
```

- **Nenhum teste pode ser removido ou "afrouxado" para passar.** Corrija a causa raiz.
- Novas regras bancárias exigem testes de regressão correspondentes.
- Mudanças de schema exigem migração; `makemigrations --check` deve ficar limpo.

## Estilo

- Python 3.13, Django idiomático; validação HTTP nas views, regras em services.
- Regras bancárias nunca no client; ledger é a fonte da verdade do saldo.
- Comentários apenas quando a lógica não é evidente.

## Mudanças sensíveis a segurança

Áreas sensíveis: autenticação/MFA, RBAC (`apps.identity`), fraud/risk gate, ledger/reconciliação, auditoria, settings/secrets. Nestes casos:

- Explique o modelo de ameaça no PR;
- Não enfraqueça checks existentes;
- O mantenedor pode exigir revisão adicional antes do merge.

## Reportando bugs

Abra issue com passos mínimos de reprodução, comportamento esperado vs observado, e logs relevantes (**sem segredos nem dados reais**). Vulnerabilidades seguem [SECURITY.md](SECURITY.md).
