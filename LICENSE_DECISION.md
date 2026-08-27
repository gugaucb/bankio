# LICENSE DECISION REQUIRED

> **Atenção do mantenedor:** o Bankio ainda NÃO possui licença open source
> escolhida. Sem um arquivo `LICENSE` com termos explícitos, o padrão de copyright
> se aplica: terceiros **não** têm direito legal de usar, modificar ou redistribuir.
> A distribuição pública como software livre fica pronta SOMENTE após a escolha
> abaixo. Remova este arquivo e adicione o texto integral da licença escolhida.

## Opções OSI compatíveis (resumo de impactos)

| Licença | Tipo | Impacto resumido |
|---|---|---|
| **MIT** | Permissiva | Uso/alteração/redistribuição quase irrestritos; exigência única: manter aviso de copyright. Máxima adoção; permite forks proprietários. |
| **Apache-2.0** | Permissiva | Como MIT + grant explícito de patentes e proteção contra reivindicações; exige preservar NOTICE e declarar mudanças. Compatível com GPLv3. |
| **GPL-3.0-only** | Copyleft forte | Quem distribuir derivados DEVE publicar o código-fonte sob GPL — garante que forks permaneçam livres; pode reduzir adoção comercial. |
| **AGPL-3.0-only** | Copyleft forte (rede) | Como GPL, mas acionável também em uso como serviço/SaaS sem distribuição — relevante para software de servidor como este. |
| **BSD-3-Clause** | Permissiva | Como MIT + cláusula de não-uso do nome do projeto para promoção. |

## Considerações específicas para o Bankio

- É uma aplicação de servidor → se o objetivo é forçar que melhorias voltem à
  comunidade mesmo sob SaaS, considere **AGPL-3.0**; se preferir adoção máxima,
  **MIT** ou **Apache-2.0**.
- O `SPDX-License-Identifier`/`org.opencontainers.image.licenses` da imagem
  Docker Hub só deve ser preenchido após esta decisão.
- Update: registre a decisão no CHANGELOG.

## Como escolher

1. Escolha a licença na tabela acima;
2. Baixe o texto oficial (https://opensource.org/licenses ou https://www.gnu.org/licenses);
3. Salve como `LICENSE` na raiz;
4. Apague este arquivo;
5. Atualize `org.opencontainers.image.licenses` no workflow de release.
