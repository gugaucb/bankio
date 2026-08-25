# FASE 8 B8 — feat/cards-advanced-notifications — Judge Report

## DESIGN
- FASE 6 presente na main ✓ → integração real, sem sistema paralelo; tudo
  via notify() central (nunca Notification.objects.create direto).
- Lifecycle (não-monetário, on_commit após o commit do controle):
  CARD_FROZEN / CARD_UNFROZEN / CARD_MARKED_LOST em set_card_control.
- Fatura: CARD_INVOICE_CLOSED por statement recém-criado no fechamento
  (on_commit) e CARD_INVOICE_DUE 1×/statement vencido via comando
  (dedup CARD_INVOICE_DUE:{statement}).
- Purchase APPROVED/DECLINED e CARD_INVOICE_PAID já existentes (B5/B7
  anteriores + FASE 6) reutilizados.
- Privacidade: declines de risco permanecem genéricos (re-provado).

## FILES
- apps/cards/services.py (+lifecycle notify) · apps/cards/billing.py
  (+_notify_closed, notify_overdue_statements) · management command
  (+overdue notify) · tests/test_cards_advanced_notifications.py (+5).

## TESTES
freeze/unfreeze/lost notificam exatamente 1× cada (double freeze dedup) ·
falha de notificação não quebra controle (cartão congela mesmo com boom) ·
fechamento notifica CLOSED com valor/due · overdue → DUE exatamente 1× em
3 execuções do comando · compra aprovada/recusada 1× · internals de risco
não vazam.

## GATES
make verify: **766 passed** · check limpo · migrations OK.

JUDGE: [✔] core reutilizado [✔] pós-commit [✔] dedup [✔] falha não-crítica
[✔] privacidade de fraude

JUDGE VERDICT: PASS
