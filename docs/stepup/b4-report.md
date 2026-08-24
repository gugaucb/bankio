# BRANCH 4 — feat/step-up-challenge-hardening — Implementation & Judge Report

## OBJECTIVE
Endurecimento do ciclo de challenge: limite de tentativas, cooldown de reemissão,
proteção brute-force, controle de concorrência e auditoria completa. TTL existente
(10 min, lazy expiry) intocado.

## DESIGN
Camada de proteção em `apps/fraud/challenge_guard.py` ao redor dos serviços core
intactos (verify_challenge/consume_challenge):

- **Brute force**: contador persistido via AuditLog CHALLENGE_FAILED (resource_id =
  challenge). MAX_ATTEMPTS=5 códigos errados → tombstone EXPIRED + audit
  CHALLENGE_EXPIRED(reason=MAX_ATTEMPTS). O código correto depois do tombstone é
  rejeitado (CHALLENGE_EXPIRED) — conhecimento posterior não ressuscita.
- **Atomicidade vs. evidência (INV 9)**: pré-checagem de material digest FORA da
  transação de lock (tombstone MATERIAL_CHANGED sobrevive ao rollback); gravação de
  falha fora da tx revertida. Pré-checagem só vale em PENDING — consume_challenge()
  reescreve material_hash após uso (anti-replay) e isso não pode ser lido como
  adulteração.
- **Concorrência**: confirm_and_consume sob select_for_update → 1 challenge → ≤1
  consumo → ≤1 settlement (testado com threads).
- **Reemissão**: challenge NOVO (mesma evaluation + material_hash, code_hash novo);
  antigo EXPIRED (código morto); cooldown 60s por customer (REISSUE_COOLDOWN);
  somente PENDING; ownership enforced; entrega pelo mesmo canal OOB; Notification
  sem código; bug corrigido onde hash e código entregue eram gerados separadamente.
- **UI**: POST da página standalone agora passa pelo guard.attempt() — brute force
  pela UI alimenta o MESMO contador do service layer.
- **Auditoria**: CHALLENGE_ISSUED/VERIFIED/CONSUMED/FAILED/EXPIRED/REISSUED —
  apenas identificadores; testado que código/code_hash/material_hash não aparecem.

## FILES
- `apps/fraud/challenge_guard.py` — camada de hardening completa.
- `apps/fraud/challenge_views.py` — fallback POST roteado ao guard.attempt().
- `tests/test_step_up_hardening.py` — 10 testes novos.

## TESTS
MAX_ATTEMPTS tombstone + código correto morto · auditorias sem segredos · abaixo do
limite o código correto liquida exatamente uma vez · reemissão (novo pk/hash, antigo
morto, novo código liquida, audit REISSUED) · cooldown bloqueia spam · reemissão
negada para consumido/estrangeiro · tamper MATERIAL_CHANGED sobrevive ao rollback ·
concorrência ≤1 confirmação · UI conta tentativas no mesmo contador.
Regressão: **503 passed**. check/makemigrations limpos.

## JUDGE
[✔] TTL existente preservado · [✔] limite de tentativas por challenge
[✔] cooldown de reemissão (novo challenge, sem spam) · [✔] brute force contido
(service + UI no mesmo contador) · [✔] concorrência: ≤1 consumo/settlement
[✔] zero movimento em challenge inválido · [✔] auditoria completa sem segredos
[✔] serviços core intactos · [✔] regressão verde

JUDGE VERDICT: PASS
