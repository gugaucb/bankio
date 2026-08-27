# SECURITY.md — Política de Segurança do Bankio

## Reportando uma vulnerabilidade

**Não abra issue pública para vulnerabilidades.**

Use o botão **"Report a vulnerability"** em *Security → Advisories* do repositório GitHub, ou contate o mantenedor diretamente (ver perfil do repositório).

Inclua: descrição, passos de reprodução, impacto estimado, versão/commit afetado. Se possível, incluir prova de conceito mínima.

## O que está no escopo

- Código fonte deste repositório (apps Django, settings, Docker/compose, workflows CI)
- Vazamento de segredos em histórico/arquivos versionados
- Bypass de RBAC/autorização, manipulação de ledger, falhas CSRF/XSS/injection

## Fora do escopo

- Ambiente de demonstração público (se houver) e dados seed fictícios
- Ataques que exijam acesso físico ao host do operador
- Relatórios genéricos de "best practice" sem impacto demonstrável
- Automatização volumétrica / DoS

## Versões suportadas

| Branch | Suportada |
|---|---|
| `main` (última release/tag) | ✅ |
| Tags antigas | ❌ — atualize |

## Expectativa de resposta

Projeto mantido por esforço próprio: não há SLA garantido. Metas informais:
acuse recebimento em até 7 dias; avaliação e correção conforme severidade e disponibilidade. Vulnerabilidades aceitas receberão crédito no advisory (a menos que prefira anonimato).
