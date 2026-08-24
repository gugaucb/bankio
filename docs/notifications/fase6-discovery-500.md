# FASE 6 — DISCOVERY: NOTIFICAÇÕES (500 perguntas, baseado no código-fonte)

Estado verificado em `main` (643 testes verdes; check/migrations OK).

---

## MODELS E ESTRUTURA (1–25)

1. **Sim.** `apps/notifications/models.py` tem o model `Notification` com migration `0001_initial`.
2. Apenas **um**: `Notification` (nada mais existe no app).
3. **Sim** — `Notification` (não existem `UserNotification`, `Alert` nem `Message`).
4. Campos de `Notification`: `recipient` (FK User, related_name="notifications", CASCADE), `category` CharField(32) default "SYSTEM", `title` CharField(140), `body` TextField blank, `read` BooleanField default False, `created_at` auto_now_add. `Meta.ordering = ["-created_at"]`. Nenhum índice além da FK.
5. **Sim** — `recipient` é FK obrigatória para o usuário destinatário.
6. **Não** — um destinatário por notificação; múltiplos = criar N linhas.
7. Parcial — só o booleano `read`; não existe status de ciclo de vida (SENT/FAILED etc.).
8. **Sim** — via booleano `read` (True/False).
9. **Não** — não existe `read_at`, `seen_at` ou equivalente.
10. **Sim** — `created_at` (`auto_now_add=True`).
11. **Não** — não há timestamp de entrega.
12. **Não** — não há timestamp de leitura.
13. **Não** — nenhum campo de prioridade.
14. **Sim** — `category` CharField(32) default "SYSTEM" (string livre, sem choices).
15. Categorias usadas no código: `"SECURITY"` (challenge_guard/challenge_delivery) e `"TRANSFER"`/`"CARD"` (apenas seed_demo); default `"SYSTEM"`. Não há enum/choices definido.
16. **Não** formalmente — apenas convenção implícita pelas strings SECURITY/TRANSFER/CARD; nenhuma regra diferencia os grupos.
17. **Não** — não existe campo JSON/metadata.
18. **Não** — não há referência estruturada à operação de origem.
19. **Não** — vínculo direto com `Transfer` não existe (só texto livre em `body`, e isso só no seed_demo).
20. **Não.**
21. **Não.**
22. **Não** — sem FK a `RiskEvaluation` (challenge_delivery menciona a operação só em prosa).
23. **Não** — sem FK a `RiskChallenge` (a criação acontece adjacente à challenge, mas sem vínculo).
24. **Não** — sem vínculo a `AuditLog`.
25. **Não** — não existe content_type/object_id (GenericForeignKey).

## SERVICES E CAMADA DE DOMÍNIO (26–36)

26. **Não** — o app não possui `services.py`, views.py ou urls.py.
27. **Nenhum.**
28. **Não.**
29. **Não.**
30. **Não.**
31. **Não** — não existe camada de domínio; quem cria usa `Notification.objects.create(...)` direto.
32. **Não** — nenhuma view do portal cria notificações.
33. **Não** — os models não criam notificações entre si.
34. **Não** — não há signals Django para notificações.
35. **Parcial** — dois services de domínio criam diretamente: `fraud/challenge_delivery.py` (`deliver()` → linha 45) e `fraud/challenge_guard.py` (reemissão → linha 174). Transfers/payments/cards **não** criam.
36. **Não** — não há convenção registrada; na prática, hoje só o domínio fraud/security cria.

## EVENTOS / ASSINCRONIA (37–44)

37. **Não** — não há event dispatcher.
38. **Não** — não há domain events.
39. **Não** — nenhum signal Django para eventos transacionais (nem post_save etc.).
40. **Não** — sem Celery/RQ/Dramatiq; nada em requirements/docker-compose.
41. **Não** — nenhuma fila interna.
42. **Não** — nenhum worker configurado (docker-compose tem só `db` e `web`).
43. **Não** — docker-compose não possui serviço de worker.
44. **Síncrona** — toda criação de Notification ocorre inline, dentro do fluxo do request/service.

## RETRY / IDEMPOTÊNCIA / DEDUP (45–56)

45. **Não** — sem retry.
46. **Não** — sem dead-letter.
47. **Não** — não se registra falha de entrega (a entrega in-app é o próprio INSERT; falha propagaria exceção).
48. **Não** — sem idempotência na criação.
49. **Não** — nenhuma constraint de unicidade (só PK e FK).
50. **Não** — sem deduplicação por operação.
51. **Não** — sem dedup usuário+tipo+referência (não há nem referência).
52. **Não** — sem janela temporal de dedup.
53. **Hoje não gera nenhuma** notificação de transferência (fluxo não integra); mas se integrar sem dedup, o replay de transferência idempotente retornaria antes do settlement (`execute_transfer` devolve `(existing, False)`), então o ponto correto de emissão (pós-settlement) naturalmente não reemitiria. Não há proteção explícita, porém.
54. **Não** — não existem testes de duplicação de notificações.
55. **Não** — sem idempotency_key no model.
56. **Não** — nenhum event_key/dedup_key/notification_key.

## FLUXOS INTEGRADOS HOJE (57–128)

57. Chamam o app notifications hoje: **somente** (a) `fraud.challenge_delivery.deliver()`, (b) `fraud.challenge_guard` (reemissão), (c) `seed_demo` (2 linhas de demo), (d) context processor `unread_notifications` (leitura), (e) testes step-up.
58–70. Transferências (enviadas/recebidas/concluídas/falhadas/bloqueadas/challenge/review/revertidas/agendadas/recorrentes): **nenhuma gera notificação** (58–70: Não em todos; exceto a nota do item 53 sobre replay).
71–76. Pagamentos (concluído/rejeitado/bloqueado/step-up/revertido): **Não** (nenhum gera).
77–93. Cartões (compra aprovada/recusada por qualquer motivo, freeze/unfreeze/perdido/emissão/limite/fatura/pagamento de fatura/vencimento/saldo insuficiente): **Não** (nenhum gera).
94–95. Empréstimos/investimentos: não existem como domínio nem notificação — **Não**.
96–102. KYC/onboarding/conta aberta/encerrada/restrita/restrição removida: **Não** (conta aberta roda risk shadow, mas não notifica).
103–121. Login/novo dispositivo/login suspeito/falhas repetidas/MFA on/off/falha MFA/troca de senha/e-mail-perfil/block/unblock admin/sessões/dispositivo revogado/challenge emitido/aprovado/rejeitado/expirado/reemitido: **Não há notificação customer-facing** — exceto **challenge emitido (117: Sim)** e **reemitido (121: Sim)**, ambos categoria SECURITY ("Verification code sent"). Os demais são cobertos por AuditLog interno, não por Notification.
122. `FraudAlert`: não gera comunicação ao cliente (**Não**) — fica restrito ao console interno de fraude.
123. RiskEvaluation HIGH/CRITICAL: **Não** comunica o cliente.
124. Operação UNDER_REVIEW: **Não** (o cliente vê estado na UI de transferência, mas não recebe Notification).
125. **Parcial**: a política está implícita — as mensagens de challenge são genéricas ("a verification code was sent… operation") e nunca citam score/regras; não há regra codificada que proíba explicitamente.
126. **Não** — nenhum score exposto.
127. **Não** — nenhuma regra disparada exposta.
128. **Não** — `RiskEvaluation` não aparece nas notificações.
129. **Baixo hoje** (pouquíssimas notificações, textos fixos), mas não há mecanismo preventivo — risco cresce quando novas integrações forem escritas.

## INTERFACE CUSTOMER-FACING (130–162)

130. **Não** — não existe central de notificações.
131. **Não** — não há rota `/app/notifications/`.
132. **Nenhuma URL** — o app notifications não tem urls.py.
133. **Não** — sem view de listagem.
134. **Não** — sem template próprio.
135. **Parcial** — o dashboard mostra badge de não lidas (ver 136); não há lista.
136. **Sim** — `templates/dashboard/shell.html` linha ~50: badge circular vermelho com contagem sobre o ícone sino.
137. **Sim** — contador de não lidas.
138. **Sim** — server-side, via context processor `apps.notifications.context_processors.unread_notifications` (registrado em config/settings.py:70): `request.user.notifications.filter(read=False).count()` **a cada request autenticado**.
139. **Não** — sem HTMX para atualizar contador.
140. **Não** — sem polling.
141. **Não** — sem WebSocket.
142. **Não** — sem SSE.
143. **Não** — nenhuma necessidade de tempo real implementada (badge atualiza só no reload).
144–159. Detalhe, marcar lida/não lida, marcar todas, apagar, retenção, soft-delete, paginação, filtros (lidas/categoria/período), pesquisa, agrupamento por data: **Não** em todos — nada disso existe.
160. **Sim** — padrão `.card divide-y` com linhas flex (usado em transactions/statement/secops).
161. **Parcial** — shell.html tem o sino+badge, mas sem link/área de destino.
162. HTMX partials existem (ex.: `transfers/_result.html`) e o padrão pode ser reutilizado; nenhuma parcial de notificação existe.

## DEEP LINKS / SEGURANÇA DE ACESSO (163–183)

163–167. Deep links para operação origem / transferência / pagamento / cartão / Central de Segurança: **Não** (não há referência nem UI).
168–173. IDOR de notificações, manipulação de ID, ownership server-side, endpoint destrutivo GET: **não aplicável ainda** — não há endpoints; tudo a construir (e o padrão do projeto é validação server-side + 404 indistinguível, cf. extrato B2/B4).
174–180. POST para ações/CSRF/mass assignment/alterar recipient/type/referência via POST: não aplicável — inexistente; CSRF global do Django já ativo nos forms existentes.
181–182. HTML de usuário em notificações/XSS: hoje os bodies são strings fixas do servidor (risco baixo); qualquer body futuro com descrição/beneficiário dependerá do autoescape (padrão ativo e testado nas telas de extrato).
183. Armazenadas como texto pronto (título+body concatenados no service chamador).
184. **Não** — não há separação dados-estruturados vs. texto apresentado.
185–187. i18n/gettext: **Não** — mensagens hardcoded em inglês (186: inglês).
188–189. Timezone: `created_at` é timezone-aware (USE_TZ padrão Django ativo); apresentação correta depende dos filtros `|date` usados nas telas.

## ATOMICIDADE / TIMING (190–206)

190. Não aplicável — nenhum fluxo monetário notifica hoje. Respostas de desenho:
191–193. Não ocorrem (nenhuma criação pré-POSTED/settlement/decisão).
194. Hoje, impossível (não há notificação de sucesso); o risco surgiria se alguém notificar dentro do bloco atômico.
195. **Não** — nenhuma notificação é criada dentro da transação de settlement hoje.
196. N/A hoje.
197. Sim, há precedência arquitetural análoga: o Risk Engine grava evidência FORA da transação de settlement (`_risk_gate` roda antes/de fora do atômico; `RISK_EVALUATION_ERROR` auditado separadamente). Esse é o padrão a seguir.
198. Falhas hoje são registradas via AuditLog (ex.: `TRANSFER_FAILED`, `RISK_EVALUATION_ERROR`), não via Notification.
199. **Sim** — ver 197: gate/evidência fora do settlement é o padrão existente.
200. Ponto correto segundo o código: após o journal POSTED e a transição de status terminal COMPLETED (`_settle` concluída / `_pay_bill_atomic` concluída), idealmente via `transaction.on_commit()`.
201. **Não** — `on_commit` não é usado em lugar nenhum do projeto (grep vazio).
202. **Não.**
203. Hoje irrelevante; para a FASE 6, sim — sem on_commit, notificar dentro do atômico notificaria antes do commit real.
204. N/A hoje (nada notifica entrada).
205. **Não há distinção formal** implementada — conceito a definir na FASE 6.
206. **Não** — nenhuma notificação nasce de clique; todas as existentes nascem de resultado de service (issue/reissue de challenge).

## AUDITLOG × NOTIFICATION (207–216)

207. **Não** — sem integração AuditLog→notifications.
208. **Não** — AuditLog não cria notificações automaticamente.
209. **Parcial** — há sobreposição conceitual (challenge emitido gera AMBOS CHALLENGE_ISSUED no AuditLog e Notification SECURITY).
210. Risco real e identificado: sem dedup/referência, Notification pode virar trilha paralela. Mitigação: Notification = comunicação customer-facing; trilha imutável permanece exclusiva do AuditLog.
211. **Não** — nenhuma regra codificada distinguindo os dois.
212. **Não** — eventos de notificação não são auditados.
213. **Não** (criar Notification não gera AuditLog hoje).
214. **Não/N-A** (não há ação de leitura).
215. **Não/N-A.**
216. Padrão atual do Bankio: auditar mutações sensíveis (mode change, export STATEMENT_EXPORTED, challenge issued/reissued). Ler notificação não precisa; marcar-lida/apagar em massa e mudanças de preferência seguem o padrão de auditar mutações de preferência (como PASSWORD_CHANGE etc.).

## PRIVACIDADE / MINIMIZAÇÃO (217–226)

217. **Não** — política formal inexistente.
218–221. Valores financeiros/conta/cartão/beneficiário completos: **hoje não** (bodies fixos; seed_demo usa "$500.00 to Alex Johnson" apenas em demo).
222. **Não** — sem mascaramento porque não há dados financeiros; o padrão de máscara do projeto existe (`•••• last4` em cards/extrato/comprovante) e deve ser reutilizado.
223–225. OTP/challenge code em Notification: **Não** — `deliver()` cria notificação dizendo que um código foi enviado, mas o código em si vai pelo logger `bankio.challenge`, nunca para a Notification.
226. **Não** — sem mecanismo de redaction.

## CANAIS (227–247)

227–231. E-mail: **Não** — sem backend SMTP configurado, sem MailHog, sem templates, nenhum envio.
232–234. SMS: **Não** — sem provider, sem simulação dedicada (o único "canal" simulado é o log do OTP).
235–238. Push/mobile/Web Push: **Não**; infraestrutura atual não justifica múltiplos canais agora.
239. Canal efetivo único: **in-app** (registro no banco + badge).
240–243. Conceito de canal/status-por-canal/retry-por-canal/fallback: **Não**.
244–245. `NotificationDelivery` / registro separado mensagem×tentativa: **Não**.
246. Já é o caso por construção — in-app independe de e-mail (que não existe).
247. **Não** — sem observabilidade de entrega (só o unread count).

## OBSERVABILIDADE / OPS (248–269)

248–253. Métricas (criadas/entregues/falhadas/retry/dedup/leitura): **Não** — nenhuma.
254. Dashboard administrativo de notificações: **Não** (existe console SecOps de fraude, unrelated).
255–257. Área de falhas/reprocessar/retry manual: **Não**.
258. Comandos manage.py de notificações: **Não** (só seed_demo toca o model).
259–260. Job periódico/scheduler de lembretes: **Não**.
261. Transferências agendadas têm job próprio (`run_scheduled_jobs` command) que poderia emitir notificações — **Sim, existe o gancho**.
262. Faturas: não há job de fechamento/vencimento — **Não**.
263–265. Infra para lembretes futuros/notificações agendadas/campo scheduled_at: **Não**.
266–268. Tarefas periódicas fora de notifications: apenas management commands executados manualmente/cron externo; sem django-q/Celery Beat.
269. Recomendação do código atual: **permanecer síncrono** — INSERT in-app é barato; `on_commit` basta; worker seria infraestrutura nova sem necessidade.

## PREFERÊNCIAS (270–292)

270–280. NotificationPreference / campo no User / página / preferências por categoria-canal-tipo (transfers/payments/cards/security/marketing): **Não** — nada existe.
281–282. Notificação obrigatória / tipos indesativáveis: **Não** definidos.
283–287. Política de alertas críticos (senha/MFA-off/novo dispositivo/bloqueio): **Não** formalizada.
288. N/A — não há preferência OFF.
289. Recomendável: sim (in-app sempre criado; OFF afetaria só canal externo futuro).
290. **Não** — distinção inexistente; a definir.
291. Unsubscribe: **Não**.
292. Risco potencial (nada impede ainda) — mitigação: whitelist de categorias obrigatórias.

## RBAC ADMINISTRATIVO (293–306)

293–297. RBAC de administração de notificações / acesso de ADMIN/MANAGER/FRAUD_ANALYST/FRAUD_MANAGER ao conteúdo: **Não existe** (e não deveria haver leitura de conteúdo individual; padrão SecOps separa métricas de conteúdo).
298. Motivo legítimo: diagnóstico de entrega agregado — não leitura de conteúdo.
299. **Não** — separação inexistente (nada implementado).
300. Risco real se dashboard mostrar conteúdo; recomendação: métricas agregadas only.
301–306. Notificação criada por admin/mensagem manual/broadcast/anúncio institucional/separação de domínio/anti-falso-alerta-de-segurança: **Não** — nada existe; se um dia existir, deve ser categoria distinta de SECURITY.

## TESTES (307–346)

307. **Parcial** — não há arquivo de teste do app em si; notificações são cobradas indiretamente pelos testes de step-up.
308. `tests/test_step_up_ui.py` (16 testes), `test_step_up_payment_resume.py` (5), `test_step_up_card_resume.py` (5) — todos consultam `Notification.objects.get(recipient=..., category="SECURITY")`.
309. Cobertura direta: ~3 asserções de Notification espalhadas; **0 testes unitários do app**.
310–311. Criação: implicitamente (step-up emite). Leitura: não.
312–315. Marcar lida/ownership/paginação/integração transfers: **Não**.
316–319. Pagamentos/cartões/segurança (além de step-up)/fraud engine: **Não** (exceto as asserções de challenge acima).
320–321. Rollback/on_commit: **Não** (on_commit nem é usado).
322–323. Idempotência/deduplicação de notificações: **Não** (idempotência financeira é testada; de notificações, não).
324. Concorrência na criação: **Não.**
325. Dois requests simultâneos criariam duas notificações iguais? Sem constraint, **sim, poderiam** — hoje mitigado apenas porque quase nada notifica.
326. **Não** — nenhuma constraint no banco.
327–337. Adversariais/IDOR/CSRF/XSS/mass assignment/replay/volume/N+1/paginação testada/ordenação/timezone de notificações: **Não** (adversariais existem para extrato, não para notifications).
338–343. Preferências/alertas-críticos-ignoram-preferências/idempotente→1-notificação/bloqueada≠sucesso/REVIEW≠concluída/reversão mantém original: **Não** (a testar na FASE 6).
344–345. Consultar notificações não altera ledger / criar notificação não altera saldo: **Não testado** (padrão de teste existe no extrato B6, reutilizável).
346. Falha de envio reverter operação? N/A hoje (sem canais externos).

## FALHAS / RESILIÊNCIA (347–356)

347. Uma exception no app pode falhar uma transferência? **Potencialmente sim** — não há try/except protegendo; hoje o risco prático é mínimo (só challenge delivery, onde a notificação vem depois do audit; uma falha ali abortaria o request pós-challenge). Para a FASE 6: criação de notificação NUNCA deve abortar settlement.
348–349. Falha de e-mail rollback payment/card? N/A — e-mail não existe.
350. Política evidenciada no código: inexistente formalmente; precedência do Risk Engine sugere "evidência/comunicação fora do caminho crítico".
351. **Não** — não há try/except silencioso em volta de notifications.
352. N/A (não há entregas ignoradas).
353. **Não** — sem DELIVERY_FAILED.
354. **Não** — dados de diagnóstico insuficientes (nada registrado).
355. Hoje não é dependência crítica; **deve permanecer não-crítica por design** na FASE 6.

## CÓDIGO MORTO / DOCS (356–377)

356. **Sim** — migration `0001_initial` existe e está aplicada.
357. **Não** — única migration, coerente com o model.
358. **Não** há código morto no app (ele é mínimo).
359. Services órfãos: nenhum (não há services no app).
360. Templates nunca utilizados: nenhum no app.
361. URLs não referenciadas: nenhuma URL existe.
362. TODOs/FIXMEs de notificações: **nenhum**.
363. Documentação: SOBRE.md cita o app numa linha da árvore (`├── notifications/ Notificações`); não há MEMORY/docs/handoff específicos.
364. **Não** — decisão arquitetural não registrada.
365. SOBRE.md é minimamente correto (o app existe) mas omite tudo que ele realmente faz (badge, challenge notifications).
366. **Sim** — badge no shell, context processor, notificações de challenge e seed_demo não estão descritos em SOBRE.md.
367. Funcionalidades descritas mas inexistentes: **Não** (docs prometem pouco).
368. Totalmente implementadas: model+migration; criação de notificação de challenge (emitida/reemitida, categoria SECURITY); unread count server-side com badge no header.
369. Parcialmente: interface customer-facing (só badge sem destino); observabilidade (count apenas).
370. Somente models: nada além de Notification (que É usado).
371. Somente services: nada.
372. Somente templates: nada.
373. Somente em testes: nada.
374. Existe mas desconectado de fluxos reais: `seed_demo` (dados fictícios) e as categorias TRANSFER/CARD dele.
375. Desativadas por configuração: nada.
376. Dependentes de infra inativa: nada (não há e-mail/SMS/push prometidos).
377. Sem nenhuma implementação: praticamente todo o escopo de uma Central de Notificações (listagem, detalhe, marcar lida, deep links, preferências, dedup, filtros, i18n, canais).

## PROVA DE IMPLEMENTAÇÃO (378–383)

378. Implementações e evidências:
   - Model: `apps/notifications/models.py::Notification`; migração `apps/notifications/migrations/0001_initial.py`.
   - Challenge emitida: `apps/fraud/challenge_delivery.py:45` (Notification SECURITY) + auditoria CHALLENGE_ISSUED; testes: `tests/test_fraud_challenge_gate.py`/step-up suites.
   - Challenge reemitida: `apps/fraud/challenge_guard.py:174` + CHALLENGE_REISSUED.
   - Badge: `templates/dashboard/shell.html:~50` + `context_processors.py` registrado em `config/settings.py:70`.
   - Demo: `apps/accounts/management/commands/seed_demo.py:157-158`.
379–380. Únicos pontos de criação transacional: `deliver()` e `challenge_guard` — ambos DEPOIS da decisão de risco e fora do settlement financeiro (não há dinheiro envolvido); portanto "antes do commit financeiro" não se aplica.
381. Se falhar: a exceção propaga ao chamador (request de step-up) — não há captura; a challenge já foi persistida antes (issue → deliver → notification), logo o estado de segurança sobrevive, mas o request pode 500.
382. Risco de duplicação: baixo hoje (challenges reemitidas criam NOVA notificação intencionalmente a cada reemissão — sem dedup, comportamento aceitável mas não especificado).
383. Testes que comprovam: `tests/test_step_up_ui.py:211,258`, `test_step_up_payment_resume.py:91`, `test_step_up_card_resume.py:96`.

## REUTILIZAÇÃO PARA A FASE 6 (384–393)

384. Reutilizar obrigatoriamente: model `Notification` (+migração existente), context processor/badge, padrão de máscara ••••last4, padrão Paginator server-side (secops/extrato), padrão card/divide-y do dashboard, AuditLog.record(), padrão IDOR-404-indistinguível do extrato, padrão adversarial do B6.
385. Estender apenas: `category` (virar choices fechadas), adicionar campos (reference/type/metadata/read_at/dedup_key) via NOVA migration — nunca tabela paralela.
386. Corrigir antes de crescer: falta de dedup_key/unicidade, ausência de camada service (create_notification única), política não-crítica (try/except+audit), on_commit para eventos monetários.
387. Eliminar da proposta da FASE 6: "criar app/model de notificações", "badge de não lidas", "contador server-side", "notificação de step-up/challenge" — já existem.
388. **Sim** — in-app puro basta; nada de SMTP/SMS/push agora.
389. Menor conjunto útil: service `notify(recipient, category, kind, payload, dedup_key)` + campos novos (kind, dedup_key unique-ish, read_at, metadata JSON) + `/app/notifications/` listagem paginada + marcar-lida POST + deep links por reference.
390–392. Integração mínima transfers/payments/cards: chamar `notify()` via `transaction.on_commit()` após status terminal COMPLETED/FAILED/REVERSED, dedup_key = `{op}:{reference}:{event}`, usando Transfer.reference/Payment.idempotency_key/journal reference/CardTransaction pk+journal ref.
393. Segurança: reaproveitar exatamente os pontos challenge_delivery/challenge_guard (já existentes) e estender a senha/MFA-off/novo-dispositivo/bloqueio nos services de identity onde o audit já ocorre.

## SEMÂNTICA DOS EVENTOS (394–409)

394. Sucesso somente pós-conclusão: transfer COMPLETED, payment COMPLETED, compra aprovada (posted).
395. Pendente: UNDER_REVIEW, STEP_UP_REQUIRED (challenge enviado), agendada criada/executando.
396. Falha: FAILED (transfer/payment), compra recusada (limite/frozen/online-off/international-off/fraud).
397. Reversão: evento PRÓPRIO vinculado ao original (nunca apagar a notificação original — espelha regra do extrato).
398. Obrigatórias (ignoram preferência OFF): senha alterada, MFA desativado, novo dispositivo, bloqueio/desbloqueio de conta, código de verificação enviado.
399. Preferências implementáveis com estrutura atual: um JSONField simples ou tabela leve por usuário×categoria.
400. Novo model/migration exigido: `NotificationPreference` (ou campo no perfil) + choices de categoria — sim, migration necessária de qualquer forma para os campos novos de Notification.
401. Worker nesta fase: **não justificado**.
402. **Sim** — `transaction.on_commit()` é suficiente para a arquitetura atual (in-app síncrono pós-commit).
403. N/A — não há mecanismo padrão de worker no projeto.
404. Ordem entre notificações da mesma operação: garantir via ordering determinístico (created_at + id, como no extrato); sem worker, ordem natural de insert preserva.
405–408. Identificador estável p/ dedup: Transfer.reference (unique) e idempotency_key (unique) ✔; Payment.idempotency_key (unique) ✔; CardTransaction → usar journal.reference (journal é único por posting) + pk; JournalEntry.reference unique ✔.
409. Reversões: `JournalEntry.reverses` + `REV-{original.reference}` dão vínculo suficiente para notificação vinculada.

## ÍNDICES / PERFOMANCE / UI (410–437)

410–412. Índices recomendados: `(recipient, read)` para o count do badge e filtro não-lidas; `(recipient, created_at)` já coberto parcialmente pela ordenação — avaliar composite; `dedup_key` unique index. Só adicionar com justificativa (padrão do projeto).
413–414. N+1 nos deep links: evitar resolvendo reference→op na view em lote (mesma técnica batched do StatementService); central em 2–3 queries previsíveis: count + page + lookup de ops da página.
415–416. Armazenar tipo + payload seguro (JSON) e renderizar via template por kind é o mais coerente com o código (evita texto pronto, permite i18n futuro e minimiza dados).
417–420. Dado controlado por usuário que pode chegar ao texto: description/beneficiary name. Autoescape do Django ativo e já provado por teste (extrato B6 XSS). Continuar sem `|safe`.
421. `|safe` em templates de notifications: **não existe** (manter assim).
422–426. API JSON: **não existe**; IDs sequenciais BigAutoField; ownership server-side torna enumeration irrelevante (padrão 404 indistinguível) — UUID não necessário.
427–432. Unread count: badge já existe; custo atual = 1 COUNT por request autenticado — aceitável na escala atual; cache desnecessário agora (projeto não usa cache).
433–437. Acessibilidade/padrões: usar links reais, focus states Tailwind existentes; badges de status seguem padrão pills (accounts.html); empty state padrão ("No … yet"); paginação Prev/Page x of y/Next (statement); filtros server-side GET (statement filters); auditoria via `audit.record(action=...)`.

## AUDITORIA / RETENÇÃO / FRONTEIRAS (438–456)

438–439. Ver 437.
440. Naming de actions AuditLog existente: UPPERCASE_SNAKE com prefixo do domínio (CHALLENGE_ISSUED, STATEMENT_EXPORTED, TRANSFER_REVERSED) → seguir (ex.: NOTIFICATION_PREFERENCES_CHANGED).
441–443. Auditar: mudança de preferências (sim), desativação de alertas não-críticos (sim, mesma action), leitura de alertas críticos (não necessário pelo padrão atual).
444–446. Retenção mínima de alertas de segurança: nada implementado; recomendação: segurança crítica = sem delete pelo usuário, apenas read.
447–448. Impedir exclusão de segurança/financeiras: razão existe (evidência); mínimo viável: bloquear delete de category SECURITY.
449–450. Histórico financeiro permanece no Extrato (FASE 5); Notification carrega deep link para a operação, nunca valores-histórico completos — a central referencia, não substitui.
451–452. Notification ≠ AuditLog (trilha imutável interna) e ≠ FraudAlert (casework interno de fraude) — manter Notification estritamente customer-facing.
453. Exclusivo do audit: evidência imutável, hashes, IPs, metadados internos.
454. Exclusivo do fraud: scores, regras, challenges, casos, console SecOps.
455. Exclusivo do ledger: saldos, journals, reversões, reconciliação.
456. Pertence a notifications: comunicação dirigida ao cliente, in-app, referenciável, marcável como lida, com preferências.

## MAPEAMENTO FASE 6 (457–480)

457. Já elimináveis: model/app, badge, unread count server-side, notificação de challenge (emitir/reemitir).
458. Só integração: transfer/payment/card/security events (chamar service novo nos pontos pós-commit).
459. Só interface: central/listagem/detalhe/marcar-lida/filtros/deep links.
460. Só testes/hardening: dedup, IDOR, XSS, rollback/imutabilidade (reusar suíte B6), concorrência.
461. Do zero: campos novos do model (migration), service de notificação, preferências, rotas/views/templates da central, on_commit hooks, testes do app.
462–467. Maiores riscos de regressão: transfers (hook em _settle/reverse_transfer), payments (_pay_bill_atomic), cards (purchase/decline paths), identity (login/password views), fraud (challenge_delivery/guard — tocar com cuidado), audit (apenas naming). Mitigação: hooks aditivos via on_commit, zero mudança nas regras financeiras.
468. Mudanças que tocariam atomic: inserir notify DENTRO dos blocos atômicos — proibir; usar sempre on_commit.
469. notification-before-commit: exatamente o risco acima — evitado por on_commit.
470. duplicate-after-retry: evitado por dedup_key único {tipo}:{reference}:{evento}.
471. notificação impedir settlement: evitado por criação pós-commit + falha tolerante (try/except com audit NOTIFICATION_ERROR, nunca silencioso).
472. Suítes que devem continuar verdes: todas (643), especialmente test_transfers, test_payments*, test_cards*, test_e2e_journeys, step-up, statement (B1–B6).
473. Baseline atual: **643 passed**.
474. Migrations pendentes: **nenhuma** (makemigrations --check limpo).
475. manage.py check: **verde**.
476. makemigrations --check: **verde**.
477–478. make verify / verify-full: alvo `verify` existe no Makefile (roda pytest+check); não há `verify-full` — executar `make verify` como gate.
479. Decisões a documentar antes de codar: Notification permanece customer-facing read-model de comunicação (não trilha); criação pós-commit via on_commit; dedup_key como chave de idempotência; categorias obrigatórias vs. preferências; sem worker/fila; armazenar kind+payload JSON, renderizar por template.
480. Ordem de branches mais segura:
   1. feat/notification-core (service + migration campos + dedup + testes)
   2. feat/notification-center-ui (central, lida, deep links, IDOR)
   3. feat/notification-transfer-integration (on_commit, dedup provado)
   4. feat/notification-payments-integration
   5. feat/notification-cards-integration
   6. feat/notification-security-events (senha/MFA/dispositivo/bloqueio + obrigatórias)
   7. feat/notification-preferences (opcional/última)
   8. test/notifications-regression (adversarial + imutabilidade)

481. Primeira branch de menor risco: **notification-core** (aditiva, não toca fluxos monetários).
482. Dependências: UI depende de core; integrações dependem de core; preferences depende de integrações (categorias reais); regression depende de todas.
483. Entregáveis independentes: payments/cards podem ser branches paralelas após core (na prática sequenciais por merge policy); security-events independe das financeiras.

## CRITÉRIOS JUDGE PASS (484–499)

484. Core: service único de criação, dedup_key enforced, falha de notificação jamais quebra chamador (auditado), migration limpa, regressão verde.
485. Transfers: COMPLETED/FAILED/REVERSED notificam exatamente 1× por destinatário; replay idempotente → 0 novas; BLOCK/REVIEW/STEP_UP nunca como sucesso; tudo pós-on_commit; testes comprovam.
486. Payments: idem, com dedup por Payment.idempotency_key; blocked por risco não notifica sucesso.
487. Cards: aprovada/recusada com motivo; declined nunca vira "concluída"; dedup por journal reference.
488. Segurança: senha/MFA/dispositivo/bloqueio emitem categoria SECURITY marcada obrigatória; sem vazamento de internals do Fraud Engine (sem score/regras).
489. Preferences: OFF não impede criação in-app de eventos obrigatórios; mudança auditada.
490. UI: listagem paginada server-side, ownership 404, marcar-lida via POST+CSRF, deep links com IDOR provado, queries constantes, badge integrado ao existente.
491–499. Invariantes a testar: consultar notificações não altera ledger (snapshot chain_hash, como B6); falha de notificação não move dinheiro; transfer idempotente ≤ 1 notificação/destinatário (assert count); reversão mantém notificação original + evento próprio; BLOCK/CHALLENGE/REVIEW jamais textualizam "concluído"; IDOR por força bruta de ids → 404; preferências OFF não suprimem SECURITY obrigatória.

## 500. MENOR CONJUNTO AUSENTE PARA A FASE 6

Considerando somente o código atual, o menor conjunto que compõe a FASE 6 sem recriar o que existe:

1. **Core**: migration em `Notification` (kind/category choices, dedup_key único, metadata JSON, read_at) + service único `notify()` com dedup, falha auditada não-crítica, e hook `transaction.on_commit()` para eventos monetários.
2. **Central**: `/app/notifications/` (lista paginada, filtros lidas/categoria, marcação lida via POST, deep links por reference reusando o detail da FASE 5), badge já existente passando a linkar.
3. **Integrações mínimas**: transfer (COMPLETED/FAILED/REVERSED/UNDER_REVIEW), payment (COMPLETED/FAILED), card (aprovada/recusada), segurança (senha alterada, MFA off, novo dispositivo, conta bloqueada — além dos challenges já existentes).
4. **Hardening/testes**: dedup/replay, IDOR, XSS, rollback/imutabilidade (padrão B6), preferências básicas com whitelist obrigatória.

Fora de escopo (infraestrutura inexistente e desnecessária agora): e-mail/SMS/push, workers/filas, WebSockets/SSE, i18n, broadcast administrativo, dashboards de entrega.
