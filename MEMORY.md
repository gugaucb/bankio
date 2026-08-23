# Bankio Ledger Hardening — Progress & Decisions

Master plan: immutable double-entry ledger → invariants → reversals/idempotency/concurrency → reconciliation → hash chain → Merkle → external blockchain anchor → proof verification → full regression.
**Blockchain = proof layer only. PostgreSQL + ledger = source of truth. No Bitcoin Account in this scope.**

## Environment
- Django 5 / Python 3.13 / Postgres / pytest / Hypothesis / Playwright
- Base branch: `main` (repo initialized fresh — no prior git history existed)
- Unified verification: `make verify` (docker compose exec web: check + makemigrations --check + pytest)

## Task Board

| # | Task | Branch | Status | Judge | Merged |
|---|------|--------|--------|-------|--------|
| 00 | Baseline commit of existing app | — | DONE | PASS | main |
| 01 | Discovery (money mutation map) | feat/ledger-01-discovery | DONE (no-diff; content in baseline commit) | PASS | main (baseline) |
| 02 | Ledger core model hardening (DB constraints) | feat/ledger-02-core-model | DONE | PASS | main |
| 03 | Atomic posting engine + property tests | feat/ledger-03-posting-engine | DONE | PASS | main |
| 04 | Balance projection & rebuild test | feat/ledger-04-balance-projection | DONE | PASS | main |
| 05 | Money boundary audit (module by module) | feat/ledger-05-money-boundary | DONE | PASS | main |
| 06 | Immutability & reversals | feat/ledger-06-immutability | DONE | PASS | main |
| 07 | Idempotency (cards/lending gaps) | feat/ledger-07-idempotency | DONE | PASS | main |
| 08 | Concurrency (PostgreSQL-proven) | feat/ledger-08-concurrency | DONE | PASS | main |
| 09 | Reconciliation service + command | feat/ledger-09-reconciliation | DONE | PASS | main (tag bankio-ledger-reconciled-v1) |
| 10 | Canonical hash + hash chain | feat/ledger-10-canonical-hash | DONE | PASS | main |
| 11 | Digital signature abstraction | feat/ledger-11-signatures | DONE | PASS | main |
| 12 | Merkle batches + proofs | feat/ledger-12-merkle | DONE | PASS | main |
| 13 | Batch chain | feat/ledger-13-batch-chain | DONE | PASS | main |
| 14 | Anchor provider interface | feat/ledger-14-anchor-provider | DONE | PASS | main |
| 15 | Simulated anchor provider | feat/ledger-15-simulated-anchor | DONE | PASS | main |
| 16 | Anchor config/policy factory | feat/ledger-16-external-anchor | DONE (external adapter = swap point; no real chain by design) | PASS | main |
| 17 | Proof verification (auditor) | feat/ledger-17-proof-verification | TODO | — | — |
| 18 | Legacy migration | — | SKIP per D002: no legacy balance columns; balances always ledger-derived. No fabricated history needed. | N/A | — |
| 19 | Dual-run transition | — | SKIP: nothing to dual-run; system was born ledger-backed. | N/A | — |
| 20 | Admin read-only protection | — | DONE within task 06 (admin read-only + tests). | PASS | main |
| 21 | Audit events for ledger/proof lifecycle | feat/ledger-21-audit | TODO | — | — |
| 22 | Adversarial testing | test/ledger-22-adversarial | TODO | — | — |
| 23 | Performance testing | test/ledger-23-performance | TODO | — | — |
| 24 | Recovery testing | test/ledger-24-recovery | TODO | — | — |
| 25 | Final regression + Judge | — | TODO | — | — |

Tags planned: `bankio-ledger-core-v1`, `bankio-ledger-reconciled-v1`, `bankio-ledger-proof-v1`, `bankio-ledger-anchored-v1`.

## Decision Log

- **D001**: Repo was NOT under git. Initialized fresh `main`; existing app committed as baseline (task 00).
- **D002**: Discovery found the app is ALREADY largely ledger-centric (no direct balance mutations found). This re-scopes several tasks:
  - Tasks 05 (money boundary) becomes an *audit/hardening* task, not a rewrite.
  - Tasks 18/19 (legacy migration/dual-run) likely N/A — no legacy balance columns exist; balances are computed from the ledger (`apps/accounts/models.py:44-52`). Will confirm and mark SKIP with justification if so.
- **D003**: Known gaps from discovery driving tasks 02–09:
  - Journal balancing validated only in Python (`apps/ledger/services.py:30-50`) — no DB CHECK constraints.
  - Idempotency missing for cards & lending operations.
  - No hash chain / Merkle / anchoring exists yet (tasks 10–17 all greenfield).
  - `Account.blocked_amount` is a stored non-ledger field — candidate for hold-ledger modeling or explicit projection policy.

## Discovery Summary (Task 01)

### Session log (2026-08-23, cont. 3)
- Tag `bankio-ledger-proof-v1` after task 17. 203 tests green.
- Task 16: settings LEDGER_ANCHOR_PROVIDER/FREQUENCY/MIN_CONFIRMATIONS + provider_factory (external adapter intentionally unimplemented until a real chain is chosen).
- Task 17 lesson: generate_merkle_proof must accept ANCHORED batches, not just SEALED.
- Remaining: 21 audit events (partially done), 22 adversarial, 23 performance, 24 recovery, 25 final regression/Judge.

### Session log (2026-08-23, cont. 2)
- Task 11: added `cryptography==44.0.1` to requirements (docker rebuild). `apps/ledger/signing.py` — DevEd25519Signer, single key boundary, swap point for KMS/HSM.
- Task 12: `apps/ledger/merkle.py` (merkle-v1, duplicate-last for odd counts) + `LedgerProofBatch` (contiguous id-range membership, signed manifests). Lessons: capture `now` once for manifest/stored sealed_at; batch seals ALL unsealed contiguous journals.
- Task 13: `verify_batch_chain` command — sequence continuity, previous_batch_hash linkage, signature validation.
- Task 14/15: `apps/ledger/anchors.py` (commitment = hash of {system, proof_version, batch_sequence, merkle_root, manifest_hash} — PII-free, tested) + `anchor_service.py` (SimulatedBlockchainAnchorProvider, LedgerAnchor state machine, FAILED->SUPERSEDED retry semantics). `anchor_ledger_batches` command.

### Session log (2026-08-23, cont.)
- Tag `bankio-ledger-reconciled-v1` after task 09.
- Task 09: `apps/ledger/reconciliation.py` + `reconcile_ledger` command; attacker-simulation test disables DB triggers to plant an unbalanced POSTED journal -> reconciliation FAILED + audit alert.
- Task 10: `apps/ledger/canonical.py` (ledger-c14n-v1 / SHA-256 / domain separators); JournalEntry gained payload_hash/previous_entry_hash/chain_hash, written in the SAME update as DRAFT->POSTED (model immutability blocks later writes). `verify_ledger_hash_chain` command detects first tampered position.

### Session log (2026-08-23)
- **Tag `bankio-ledger-core-v1` created after task 08** — all core ledger gates pass, 156 tests green on main.
- Task 04: `account_balance` now filters to POSTED journals only (drafts must not affect balances); DB constraint on blocked_amount >= 0.
- Task 05: static scan test proves no balance writes anywhere; Account has no stored balance column; e2e transfer journal+audit verified. Note: transfer journals DEBIT sender / CREDIT receiver (customer accounts are liabilities).
- Task 06: posted journal ROWS now DB-immutable (only `reverses` link may change; deletes blocked). Admin already read-only.
- Task 07: new `LedgerIdempotencyRecord` model; wired into card purchase / statement payment / loan disburse / repay with replay semantics.
- Task 08 lessons: idempotency check MUST come after row-lock acquisition; `select_for_update` cannot traverse nullable FKs via select_related; services that re-fetch under lock must copy state back onto caller's instance (approve/repay staleness bug found by regression).
- Task 02 merged: DB CHECK constraints + Postgres triggers (balanced-post trigger, posted-entry immutability triggers). 9 new bypass-proof tests. Lesson: `RunSQL` needs SQL strings, not functions.
- Task 03 merged: `LedgerAccount.status` (ACTIVE/BLOCKED/CLOSED), `JournalEntry.currency`; `post_journal` rejects non-active accounts, mixed currencies, invalid sides, empty journals. Property test for multi-line journals. 132 tests green on main.

Ledger models already present (`apps/ledger/models.py`): LedgerAccount (ASSET/LIABILITY/INCOME/EXPENSE/EQUITY), JournalEntry (DRAFT→POSTED, unique reference, reverses FK), LedgerEntry (DEBIT/CREDIT, positive Decimal(19,2)).

Money Mutation Map (all via ledger journals):
| MODULE | OPERATION | BEHAVIOR |
|---|---|---|
| transfers | internal/external transfer, scheduled batch | journal + idempotency_key + select_for_update (services.py:53-244) |
| payments | pay_bill | journal + idempotency_key + lock (services.py:17-47) |
| lending | disburse / repay_installment | journal, NO idempotency key (services.py:74-126) |
| cards | purchase / pay_statement | journal, checks available_balance, NO idempotency key (services.py:71-161) |
| investments | place_order buy/sell | journal + idempotency_key (services.py:17-73) |
| accounts | balances | pure properties derived from ledger (models.py:44-52) |
| managerops | account opening | get_or_create LedgerAccount, no money movement |

Audit: `AuditLog` immutable model exists; no ledger/proof lifecycle events yet.

Risks: no DB-level balanced-journal constraint; immutability enforced only via save() overrides (bypassable via queryset update/raw SQL); blocked_amount non-ledger; no crypto layer at all.
