# S5.B4 — feat/transaction-details-receipts — Judge Report

## DESIGN
- Detalhe: `/app/transactions/<reference>/` — resolve JournalEntry pela
  reference pública; ownership verificado via ledger entries → conta do
  usuário (IDOR → 404 indistinguível de inexistente).
- Agregador único justificado: não existiam páginas de detalhe para
  transfer/payment/card; a view despacha por FK journal e expõe apenas
  campos reais. Nunca mostra ledger ids internos, hashes, idempotency keys.
- Comprovante: `/app/receipts/<reference>/` — READ-ONLY, sem persistência,
  apenas para operações efetivadas (status COMPLETED/REVERSED, journal
  POSTED, card não declinado). Identidade = journal/operation reference
  real, nunca identificador aleatório.
- Reversão: original mantém referência visível + link "View reversal";
  estorno tem "View original operation". Status REVERTED quando
  journal.reversed_by existe.
- Statement rows agora linkam ao detalhe via operation_reference.

## DOMAIN FIX (justificado)
- `transfers.services._settle` nunca persistia Transfer.journal (transition()
  só salva status/updated_at) — vínculo operação→ledger nulo no banco.
  Fix mínimo: `transfer.save(update_fields=["journal"])`. Nenhuma regra
  financeira alterada; regressão completa verde.

## FILES
- apps/transfers/services.py (fix) · apps/identity/app_views.py (+3 views) ·
  urls.py (+2 rotas) · templates/dashboard/transaction_detail.html,
  receipt.html (novos, receipt print-friendly sem motor PDF) ·
  statement.html (links) · tests/test_statement_details.py (+7).

## TESTES
detalhe owner 200 c/ status COMPLETED e sem vazamento de internals ·
receipt completed com contraparte mascarada (•••• last4, número completo
ausente) · journal sem origem → receipt 404, detail ok · links origem↔
estorno nas duas direções · original preservado após reversão · IDOR
detail+receipt+tampered ref → 404 · views read-only (chain_hash e saldo
intactos). Regressão: **628 passed**. check limpo. migrations OK.

## JUDGE
[✔] comprovante só p/ operação válida · [✔] identidade vem da operação ·
[✔] IDOR protegido · [✔] dados sensíveis minimizados/mascarados · [✔]
comprovante não é ledger (read-only provado) · [✔] reversal não apaga
original · [✔] regressão verde

JUDGE VERDICT: PASS
