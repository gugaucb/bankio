# BROWSER SURFACE MAP — BankIO (descoberta em código, branch test/browser-e2e-baseline)

Legenda: Role = quem acessa; Fin = relevância financeira; Sec = relevância de segurança; HTMX onde existe.

## Público (portal) — apps/portal
| Route | Role | Screen | Actions/Forms | Fin | Sec | Browser test |
|---|---|---|---|---|---|---|
| / | anônimo | home institucional | navegação | – | – | smoke |
| /personal/ /business/ /cards/ /investments/ /loans/ /security/ /help/ | anônimo | marketing | – | – | – | smoke (1 representativa) |
| /open-account/, wizard steps | anônimo | onboarding wizard | forms por step | sim | sim | smoke landing |
| /manager/login/, /manager/login/otp/ | manager | login institucional + OTP | form + otp | – | alto | journey |

## Auth — apps/identity
| Route | Role | Screen | Actions | Fin | Sec | Browser test |
|---|---|---|---|---|---|---|
| /login/ | anônimo | login (username/password) | POST; risco→OTP | – | alto | sucesso, inválido, bloqueio (5 falhas), OTP/MFA |
| /otp/ | pending session | OTP 6 dígitos | POST code | – | alto | via login MFA |
| /logout/ | auth | – | GET link/POST | – | médio | journey |

## Customer app (/app/*) — @login_required + customer_only
| Route | Screen | Forms/Ações | HTMX | Fin | Sec |
|---|---|---|---|---|---|
| /app/ | dashboard (saldo, quick transfer, gráficos) | quick transfer form | hx-post /transfers/ | alto | alto |
| /app/analytics/ | analytics | – | – | baixo | – |
| /app/accounts/ | contas | – | – | alto | alto |
| /app/accounts/{id}/statement/ | extrato | filtros period/from/to/direction/source/q; paginação | – | alto | IDOR alvo |
| …statement/export.csv | CSV download | – | – | alto | IDOR |
| …statement/print/ | print view | window.print | – | baixo | IDOR |
| /app/transactions/ | transações | filtros | – | alto | – |
| /app/transactions/{ref}/ | detalhe | – | – | alto | IDOR |
| /app/receipts/{ref}/ | comprovante | – | – | alto | IDOR |
| /app/investments/ | posições | – | – | baixo | – |
| /app/cards/ | lista cartões | request card? | – | alto | IDOR |
| /app/cards/{id}/ | detalhe | freeze/unfreeze, toggle_online, toggle_international, report_lost (POSTs) | – | alto | IDOR+CSRF |
| /app/cards/{id}/transactions[/tx] | histórico/detalhe | filtros | – | médio | IDOR |
| /app/cards/{id}/invoices/ | faturas | **pay form POST** (pay open invoice) | – | alto | IDOR+double-submit |
| /app/cards/{id}/invoices/{sid}/ | fatura detalhe | composição | – | médio | IDOR |
| /app/security/ | senha, MFA enable/disable, devices, sessões | POSTs múltiplos | – | – | muito alto |
| /app/settings/ | settings | – | – | baixo | – |
| /app/notifications/ | central | mark read, read-all, preferências mute/unmute, deep links | – | baixo | IDOR read |

## Transferências — apps/transfers
| Route | Screen | Observação |
|---|---|---|
| /transfers/ | form + histórico | HTMX result; step-up inline quando CHALLENGE |
| /transfers/quick/ | form parcial | HTMX include no dashboard |
- UI existe: interna (destination_account), externa (beneficiary is_external). Saldo insuficiente → mensagem.
- SEM UI: agendada/recorrente/reversal/approval (service/command only) → não testável em browser.

## Admin — apps/identity/admin_views (staff)
/manage/users/ (lista+busca), /manage/users/dashboard/, new/, {id}/, block/, unblock/ — POSTs.

## ManagerOps — apps/managerops (staff MANAGER)
dashboard, customers search, customer360, onboarding, open-account, approvals queue+decide, restrictions apply/lift, notes, service requests, card-requests decide. Maker-checker via approvals.

## Fraud/SecOps — apps/fraud (staff)
/fraud/ dashboard, /fraud/alerts/ (+acknowledge/open-case POST), /fraud/cases/{id}/ (+decide), /security/challenge/{id}/ (step-up OTP), /secops/health|mode|evaluations.

## Funcionalidades SEM tela (não testar em browser)
Pagamentos UI (apps/payments sem urls/templates); compra de cartão UI; transferência agendada/recorrente UI; reversal/approval UI; PAN/CVV nunca exibidos.

## Segurança em browser (obrigatória)
Não autenticado → redirect login; role errada (customer→/fraud/, staff-only pages para customer) → 403/404; IDOR statement/receipt/card/invoice/notification entre usuários → 404 indistinguível; POST sem CSRF → rejeitado; ações destrutivas só via POST.
