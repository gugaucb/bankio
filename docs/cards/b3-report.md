# FASE 8 B3 — feat/cards-limits-availability — Judge Report

## DESIGN
- available_limit DERIVADO (helpers credit_used/credit_availability do B1):
  usado = aprovadas − statements pagos; disponível = credit_limit − usado,
  nunca negativo. SEM segunda coluna mutável de available_limit.
- Compra afeta disponibilidade exatamente 1× (lock do card +
  record_idempotent); replay idempotente não reduz novamente.
- Mudança de limite permanece exclusiva de manager (decide_card_request);
  set_card_control NÃO aceita credit_limit; UI não permite injeção.
- Reversal/refund de compra: ❌ não existe no domínio — documentado FORA DE
  ESCOPO (§5/§20 condicional); restauração por disponibilidade provada via
  statement pago existente.

## FILES
- tests/test_cards_limits.py (+8). Nenhuma mudança de código de produção
  (helpers B1 já proviam a derivação; autoridade de limite já correta).

## TESTES
compra reduz 1× · replay não reduz · decline zera efeito · exatamente no
limite ok / acima declina · nunca negativo · statement pago restaura
disponibilidade + pay_statement replay seguro · CONCORRÊNCIA: duas compras
simultâneas de 70 contra limite 100 → no máximo 1 liquida, used ≤ limit ·
cliente não seta credit_limit via service nem via POST (mass assignment).

## GATES
make verify: **737 passed** · check limpo · migrations OK.

JUDGE: [✔] fonte única de verdade [✔] compra 1× [✔] decline/replay zero
[✔] overspend race impossível [✔] autoridade de limite preservada

JUDGE VERDICT: PASS
