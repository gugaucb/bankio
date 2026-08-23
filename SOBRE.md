# SOBRE.md — Bankio: Plataforma Bancária Modular com Fraud & Risk Engine

> Documento de handoff: resumo completo do projeto para dar a uma LLM (ou
> engenheiro) o entendimento do todo — funcionalidades, estratégia,
> arquitetura, stack e decisões. Atualizado em 2026-08-23.

---

## 1. Visão geral

**Bankio** é um banco digital simulado (monolito modular Django) construído
sobre um **ledger de partidas dobradas imutável e verificável**, com um
**Fraud & Risk Engine determinístico, versionado e auditável** que avalia
toda operação sensível ANTES de produzir efeito financeiro irreversível.

Princípios fundantes:
- **O saldo nunca é uma coluna mutável** — é projeção derivada do ledger.
- **Nada entra no ledger sem passar pelo gate de risco** quando em enforcement.
- **Separação estrita de responsabilidades**: RBAC ≠ regras de negócio ≠
  fraude/risco ≠ ledger ≠ reconciliação ≠ auditoria.
- **Um repositório, um backend, um banco, um app** — sem microserviços,
  Kafka, Node ou decisões de risco no client.

## 2. Stack

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.13 |
| Framework | Django 5.1 (server-rendered) |
| Banco | PostgreSQL (psycopg 3) |
| Frontend | Templates Django + HTMX + Tailwind CSS |
| Criptografia | `cryptography` (hash chain, Merkle, assinaturas, âncoras) |
| Testes | pytest + pytest-django + Hypothesis (property-based) + Playwright |
| Deploy dev | Docker Compose (`web` + Postgres), gunicorn para prod |
| Qualidade | `make verify` / `make verify-full`; `manage.py check`, `makemigrations --check` |

## 3. Apps (monolito modular)

```
apps/
├── identity/      Usuário custom (roles), login com OTP, dispositivos,
│                  dashboards do cliente, troca de senha
├── customers/     Perfis de cliente, KYC básico
├── accounts/      Contas (CHECKING/SAVINGS/SALARY/JOINT/BUSINESS),
│                  beneficiários; services de abertura observada por risco
├── ledger/        NÚCLEO: LedgerAccount, JournalEntry (imutável, hash chain),
│                  posting engine idempotente, balances por agregação,
│                  reconciliação, Merkle batch, assinaturas, âncora externa
├── transfers/     Transferências internas/externas, agendamento, reversão,
│                  idempotência, concorrência (select_for_update)
├── payments/      Pagamento de contas/faturas liquidado no ledger
├── cards/         Cartões débito/crédito, controles (online/internacional),
│                  compras simuladas como adquirente, congelamento, perda
├── investments/   Produtos de investimento
├── lending/       Crédito/empréstimos
├── compliance/    KYC/AML, mini-engine legado evaluate_fraud (aposentado do fluxo)
├── managerops/    Portal do gerente: onboarding, abertura de conta com KYC,
│                  restrições (AML/legal hold só compliance), matriz de
│                  autoridade, maker-checker, isolamento por agência
├── portal/        Views públicas + login institucional do gerente (OTP)
├── support/       Suporte
├── notifications/ Notificações
├── audit/         AuditLog imutável (actor, ação, IP, device, metadata)
└── fraud/         Fraud & Risk Engine v1 (detalhado na seção 6)
```

## 4. Funcionalidades de negócio

- **Onboarding**: cadastro de cliente por gerente com detecção de duplicidade;
  ativação de conta gated por KYC aprovado.
- **Contas**: múltiplos tipos e moedas (USD no core), limites transacional e
  diário, bloqueios/restrições, número único gerado server-side.
- **Transferências**: internas (conta-conta) e externas (beneficiário
  verificado), agendadas/recorrentes, reversão controlada, idempotência por
  chave (replay retorna o original), testes de concorrência contra double-spend.
- **Pagamentos**: liquidação de boletos/contas via journal DEBIT/CREDIT.
- **Cartões**: emissão via request/approval, compra simulada com hard controls
  (estado, limits, flags online/internacional), extrato e pagamento de fatura.
- **Portal do gerente**: login dedicado MANAGER+OTP, dashboard, aprovações
  maker-checker (solicitante nunca aprova), mudanças de limite com matriz de
  alçada, encerramento de contas (saldo zero, sem restrição ativa).
- **Console de fraude**: fila de alertas com dedup, casos investigativos,
  gestão versionada de regras, métricas do engine.
- **Auditoria**: todo evento sensível gera AuditLog imutável.

## 5. Arquitetura do ledger (fase 1)

- `JournalEntry` POSTED/DRAFT; entradas postadas são imutáveis (triggers).
- Toda movimentação = journal balanceado de 2+ linhas (DEBIT/CREDIT).
- Saldo = `SUM(debits) - SUM(credits)` sobre journals POSTED apenas.
- Idempotência de posting por chave única (replay seguro).
- Hash chain canônico + batches Merkle + assinatura + âncora externa simulada
  → prova de integridade verificável (`verify` services + comandos).
- Concorrência: `select_for_update` na conta-fonte; testes com threads.

## 6. Fraud & Risk Engine v1 (apps/fraud)

### Ciclo de decisão
```
REQUEST → AUTH → AUTHZ → validação de negócio
       → RiskContext → coleta de sinais → regras → score → política
       → ALLOW / CHALLENGE / REVIEW / BLOCK
```

### Componentes
- **RiskEvaluation**: snapshot imutável da decisão (score, nível, decisão,
  versões de policy/ruleset, regras disparadas, valores de sinais,
  engine_mode, idempotency_key). RiskSignal = fato por sinal.
- **Sinais (~25)**: registry puro e determinístico com isolamento de erro por
  sinal (`{"__error__": ...}`). Montante, hora, idade de conta/cliente,
  dispositivo (novo/primeiro visto/compartilhado), velocidades de
  transferência (10min/1h/24h, totais diários), beneficiários novos,
  sinais de auth (IP diferente, velocity de login, falhas MFA), cartão
  (velocity, spend diário, sequência rápida).
- **Regras**: JSON estruturado de condições (AND) sobre sinais; operadores
  is/is_not/gt/lt/ge/le/in/not_in; versionadas (DRAFT→TESTING→APPROVED→
  ACTIVE→RETIRED), maker-checker (auto-aprovação bloqueada), simulação
  contra histórico antes de ativar; digest do ruleset.
- **Score**: soma clampada [0,100]; faixas LOW<30/MEDIUM<60/HIGH<80/
  CRITICAL; property tests com Hypothesis.
- **Políticas**: mapas nível→decisão por operação (DEFAULT, LOGIN,
  PROFILE_UPDATE); não mapeado cai em REVIEW (fail-safe). Versão `policy-v1`.
- **Modos**: DISABLED/SHADOW/CHALLENGE_ONLY/ENFORCEMENT; troca somente via
  caminho auditado e permission-gated (FRAUD_MANAGER) — comando
  `manage.py fraud_mode` ou console. SHADOW nunca interfere; CHALLENGE_ONLY
  degrada REVIEW/BLOCK → challenge.
- **Step-up challenge**: código 6 dígitos hash, TTL 10min, single-use,
  vinculado a material_hash dos fatos da operação (amount/beneficiário/chave);
  alteração de fatos após emissão mata o desafio.
- **Alertas**: dedup operação:cliente:regras em janela de 60min; correlação
  em caso.
- **Casos**: máquina de estados com estados terminais absorventes, timeline
  append-only; **único lugar onde se confirma fraude** (risco ≠ fraude confirmada).
- **RBAC/SoD**: FRAUD_ANALYST < SENIOR < FRAUD_MANAGER; analistas não gerem
  regras/policies/modo e jamais tocam saldos.
- **Baselines & correlações**: baseline comportamental por cliente (mínimo 5
  observações para ser "confiável"), ATO correlation (fatores distintos
  super-lineares em 48h), insider risk (volume alto, self-dealing,
  resultados negativos repetidos).
- **Fail-safe matrix (`failsafe-v1`)**: TRANSFER/CARD/BILL/OPENING = fail-open
  com auditoria; LOGIN = fail-closed; desconhecido = fail-closed. Falha do
  motor nunca vira ALLOW silencioso (snapshot FAILED/DEFER + audit).
- **Observabilidade**: `engine_metrics` (distribuição de decisões/status,
  erros, latência p50/p95 vs budget 200ms), `challenge_metrics`,
  `false_positive_report` (taxa de intervenção + casos contestados;
  precision/recall honestamente None sem labels).
- **Backtesting**: replay das regras candidatas sobre snapshots armazenados
  (`backtest_shadow`, `simulate_rule`) com gate de enforcement
  (block ≤5%, review ≤20%).

### Pontos de integração (enforcement atual)
| Fluxo | Modo efetivo | Comportamento |
|---|---|---|
| Transfer | ENFORCEMENT total | BLOCK→FAILED row+audit; CHALLENGE→STEP_UP_REQUIRED; REVIEW→UNDER_REVIEW |
| Bill payment | ENFORCEMENT total | PaymentError RISK_BLOCKED / STEP_UP_REQUIRED |
| Card purchase | ENFORCEMENT total | decline RISK_BLOCKED / STEP_UP_REQUIRED |
| Login, senha/profile, abertura de conta, ops de gerente | Observacional | grava avaliação; fail-safe por operação |

Detalhe arquitetural importante: nos fluxos monetários o **gate roda FORA da
transação atômica de settlement**, para que evidências (avaliação, linha
FAILED, challenge) sobrevivam ao rollback da operação abortada.

### Invariantes (1–10)
1. Client não define própria decisão (tudo server-side).
2. Decisões derivam de sinais/regras versionados no servidor.
3. Versões históricas de regras/políticas retidas; snapshots guardam o que foi aplicado.
4. BLOCKED = zero movimento no ledger.
5. Challenge vinculado aos fatos da operação (material hash).
6. Risco ≠ fraude confirmada; só FraudCase confirma.
7. Mudanças de regras auditáveis (lifecycle + maker-checker).
8. Fraude não sobrescreve invariantes do ledger.
9. Falha explícita: nunca silenciosa, nunca vira ALLOW sem registro.
10. Analistas não tocam saldos nem policies.

## 7. Estratégia de evolução e qualidade

- Progressão obrigatória OBSERVE → SHADOW → BACKTEST → CHALLENGE_ONLY →
  LIMITED ENFORCEMENT → FULL ENFORCEMENT, cada etapa medida antes da próxima.
- Tags de release: `bankio-fraud-engine-v1`, `-shadow-v1`, `-challenge-v1`,
  `-enforcement-v1` (+ tags do ledger fase 1).
- Protocolo por tarefa: descoberta → critérios → branch `feat/*` →
  implementação → testes → ataques adversariais → backtest → juiz; merge só
  com veredito PASS via `git merge --no-ff`; main = último estado aceito.
- **405 testes verdes** cobrindo domínio, propriedades (Hypothesis),
  concorrência, adversarial, integrações e E2E de serviços.

## 8. Limitações honestas (registradas)

- Sem ground-truth labels de fraude: precision/recall = None; FP medido por
  proxies (taxa de intervenção, casos contestados).
- Sinais de geolocalização/mudança de contato deliberadamente ausentes (sem
  fonte de dados — decisão D-F04; não fabricar sinais).
- Step-up challenge completo no backend, mas sem tela customer-facing ainda
  (CHALLENGE_ONLY interrompe com STEP_UP_REQUIRED).
- Backtest em dados de dev valida a ferramenta; rodar em tráfego real antes
  de apertar thresholds.

## 9. Como rodar

```bash
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec -T web pytest -q                 # suíte completa
docker compose exec -T web python manage.py check
docker compose exec -T web python manage.py fraud_mode   # lê/troca modo
docker compose exec -T web python manage.py backtest_shadow
```

Documentação adicional: `MEMORY.md`, `MEMORY_FRAUD.md` (board das 41 tarefas),
`docs/fraud/*.md` (surface map, relatório de backtest, regressão, aceite).
