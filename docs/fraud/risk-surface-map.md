# Bankio Risk Surface Map — Task 01 Discovery

Fonte: varredura do codebase @ main 41dee3b (Fase ledger concluída, 217 testes verdes).

## Operações protegíveis

| Operação | Local | Efeito financeiro/privilegínio | Risco principal | Proteção atual |
|---|---|---|---|---|
| Login / MFA | identity/services.py `attempt_login`, `verify_otp` | sessão | Account takeover | lockout 5 falhas/15min; OTP p/ novo device se MFA ativo |
| Password change/reset | identity/app_views.py `security_view` | credencial | ATO | senha antiga obrigatória; audit PASSWORD_CHANGED |
| Device trust | identity `Device`, `is_new_device()` | sessão/MFA bypass | device spoofing | hash UA+lang; flag trusted |
| Transferência | transfers/services.py `execute_transfer` | **ledger posting** | perda direta | limites tx/daily, idempotency_key, `evaluate_fraud` (compliance), row locks |
| Transfer agendada | `process_due_scheduled` | **ledger posting** | execução sem reavaliação de risco | re-check saldo apenas |
| Aprovação transfer UNDER_REVIEW | `approve_transfer` | **ledger posting** | insider abuse | manager-only, re-check saldo |
| Beneficiário novo | accounts.Beneficiary | pré-fraude | fraude preparatória | verified flag (sem workflow de verificação) |
| Compra no cartão | cards/services.py `purchase` | **ledger posting** | gasto não autorizado | status/limits/fundos; **sem avaliação de risco** |
| Limite/pedido cartão | `request_card`, `decide_card_request` | exposição de crédito | insider/excesso | manager-only, max $20k |
| Freeze card | `set_card_control`, `report_lost_or_stolen` | controle | abuso | owner-only |
| Pagamento de fatura | `pay_statement` | **ledger posting** | — | idempotente, locks |
| Empréstimo: apply/score | lending/services.py `apply_for_loan` | crédito | fraude de crédito | score ≥550 p/ aprovar; **sem audit** |
| Disburse / repay | `disburse`, `repay_installment` | **ledger posting** | duplo desembolso | idempotency keys, locks; **sem audit** |
| Bill pay | payments/services.py `pay_bill` | **ledger posting** | — | idempotente; **sem risco** |
| Abertura de conta | managerops + portal `submit_application`/`decide_application` | conta+ledger account | fraude de identidade | KYC gating, duplicate detection, risk assessment simples |
| Onboarding customer | managerops `create_customer` | identidade | identidade falsa | duplicata email/telefone, idade ≥18 |
| Mudanças de perfil | email/phone: **sem endpoints visíveis** | credencial/MFA | phone-change ATO | ausente (gap) |
| Approvals maker-checker | managerops/services.py | privilégio | insider | níveis de autoridade por valor, self-approval bloqueado |
| Restrictions | apply/lift_restriction | acesso a fundos | bypass AML | AML/LEGAL hold só compliance |
| Django admin | /admin/ | tudo | escalação total | **sem scope check (CRITICAL gap)** |

## Motor de fraude existente (a substituir/absorver)
- `apps/compliance/services.py::evaluate_fraud` — chamado SÓ em execute_transfer.
- Regras: AMOUNT_ABOVE, VELOCITY(10min), NEW_DEVICE_HIGH_VALUE (**stub vazio**).
- Ações: BLOCK (transfer FAILED) ou REVIEW (UNDER_REVIEW → manager).
- `FraudAlert` criado por regra disparada.
- Decisão: Fase 2 constrói `apps/fraud` como plataforma completa; `evaluate_fraud` será migrado para o novo engine na integração de transfers (Task 12) e desativado depois.

## Gaps relevantes ao fraud engine
1. Fraude avaliada só em transfers — cards/payments/loans/login não passam pelo engine.
2. Sem rate limiting de request (só lockout de login).
3. Sem tracking de mudança de email/telefone (signal PHONE_CHANGED impossível hoje — criar endpoints sinalizados ou tratar como N/A).
4. Loan/payment ops sem audit events.
5. Sem labels históricos de fraude → backtesting reportará métricas de distribuição, não precision/recall (spec §55).
6. Device fingerprint frágil (UA+lang) — aceitável como signal, não como controle.

## Veredito Discovery Gate
PASS — todas as rotas críticas de dinheiro e privilégio identificadas acima.
