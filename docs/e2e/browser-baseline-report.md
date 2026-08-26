# BROWSER BASELINE REPORT — BankIO E2E em navegador real

Branch: `test/browser-e2e-baseline` · Stack: Python + Playwright 1.49 + Chromium
(host) contra stack compose ao vivo (`http://localhost:8000`, seed_demo).
Suite: `e2e_browser/` (fora do `testpaths` do pytest.ini — o `make verify`
do container permanece intocado). Comando:

```
python3 -m pytest -c e2e_browser/pytest.ini e2e_browser -q \
  --screenshot=only-on-failure --output=e2e_browser/screenshots
```

Total screens mapeadas: 45+ rotas (docs/e2e/surface-map.md)
Total journeys: 56 testes em navegador real
PASS: 54 · FAIL: 0 · BLOCKED/xfailed (defeitos documentados): 2

| Área | Status |
|---|---|
| Auth (login ok/inválido/lockout 5 falhas/OTP-MFA/logout) | PASS (6) |
| Customer (dashboard/contas/analytics/investments/settings) | PASS |
| Extrato (listagem/filtros/busca/paginação-abuse/detalhe/comprovante/CSV download/print) | PASS (7) |
| Transfers (interna HTMX c/ prova de ledger, saldo insuficiente, valor inválido, histórico, externa/beneficiário) | PASS (5) |
| Cards (lista/detalhe mascarado/freeze-unfreeze/toggle online/faturas/pagamento fatura) | PASS (6) |
| Security (MFA enable/disable via OTP, sessões revoke-all, histórico, senha) | PASS* |
| Notifications (badge/central/mark-read/read-all/dedup) | PASS (3) |
| Admin (users busca/dashboard/create/block→unblock c/ confirm dialog) | PASS |
| Manager (dashboard/customers/customer360 por assignment/approvals/restrictions/card-requests/onboarding) | PASS |
| Fraud/SecOps (dashboard/alerts para staff) | PASS |

Console errors / pageerror / HTTP ≥500 falham o teste automaticamente
(fixture `browser_guards`); screenshots on failure ativados.
Viewports: smoke Desktop 1440x900 e Mobile 390x844 (sem overflow >40px).

## SEGURANÇA EM NAVEGADOR REAL
- Não autenticado → redirect a login em todas as áreas privadas ✅
- Role errada (customer → /fraud/, /manage/*, /secops/*) → **403** ✅
- IDOR: statement/export/print, transaction detail, receipt, cards,
  card transactions, invoice detail, POST controls/pay de outro usuário →
  **404 indistinguível** ✅ (nota: comprovante de transferência RECEBIDA é
  visível ao destinatário — correto, journal é compartilhado)
- URL manipulada (ids inexistentes/ref inválida) → 404 ✅
- POST sem CSRF (fetch same-origin sem token) → **403** ✅
- Destrutivas só via POST; GET ?action=freeze não muta estado ✅
- CSRF findings: nenhum. IDOR findings: nenhum vazamento.

## DEFECTS ENCONTRADOS (reais, pré-existentes)
1. **DEFECT #1 — destino numérico tratado como PK** (`apps/transfers/views.py:21`):
   `dest_raw.isdigit()` → lookup por `pk`; todos os account numbers são
   numéricos, então digitar o número da conta não acha a conta e a
   transferência vira EXTERNA via clearing. Journey interno testado pelo
   contrato funcional (PK). Corrigir lookup para aceitar account_number.
2. **DEFECT #2 — troca de senha impossível na UI**: `/app/security/` renderiza
   `{{ form.as_p }}` sem `form` no contexto GET → campos nunca aparecem.
   Pinado em `test_change_password_form_renders_fields`.
3. **DEFECT #3 — busca de customers do manager quebra**: `/manage/customers/?q=…`
   retorna **HTTP 500**. Pinado com `xfail(strict=True)`.

## Visual defects
Nenhum bloco quebrado detectado; mobile sem scroll horizontal relevante.

## RESOLUÇÃO (fix/browser-defects-trio)
Os 3 defects foram corrigidos e os pins viraram testes reais:
- #1: `apps/transfers/views.py` — destino numérico resolve por account_number
  primeiro (fallback pk); regressão Django em tests/test_transfers.py.
- #2: `apps/identity/app_views.py` — `ChangePasswordForm` no contexto GET.
- #3: `apps/managerops/views.py` — filtro de busca via user__* /
  user__accounts__account_number (Customer não tem accounts direto).
Gates: make verify **775 passed** (+1 regressão) · browser **55 passed,
0 xfail**.

# BROWSER BASELINE VERDICT: PASS
(ALL CRITICAL BROWSER JOURNEYS = PASS após correções.)
