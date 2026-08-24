"""Transfer domain service: the single entry point for money movement."""
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from apps.accounts.models import Account, AccountStatus
from apps.audit.services import record as audit
from apps.ledger import services as ledger

from .models import Transfer, TransferStatus


class TransferError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _require_active(account):
    if account.status != AccountStatus.ACTIVE:
        raise TransferError("ACCOUNT_NOT_ACTIVE", f"Account {account.account_number} is {account.status}")


def _validate_amount(amount_raw):
    try:
        amount = Decimal(str(amount_raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise TransferError("INVALID_AMOUNT", "Invalid monetary amount")
    if amount <= 0:
        raise TransferError("INVALID_AMOUNT", "Amount must be positive")
    return amount


def daily_outflow_total(source_account, now=None):
    now = now or timezone.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return (
        Transfer.objects.filter(
            source_account=source_account,
            status=TransferStatus.COMPLETED,
            created_at__range=(start, end),
        ).aggregate(t=Sum("amount"))["t"]
        or Decimal("0")
    )


def _risk_evaluation(actor, source, amount, destination, beneficiary, idempotency_key):
    """Run the fraud engine on every transfer attempt.

    The stored evaluation is correlated with the transfer idempotency key.
    Engine errors are audited and non-fatal (fail-open per failsafe-v1);
    the returned evaluation drives _risk_gate below.
    """
    from apps.fraud.context import RiskContext
    from apps.fraud.engine import evaluate_operation

    try:  # raw input may not be numeric yet; validation owns that error
        safe_amount = Decimal(str(amount)) if not isinstance(amount, Decimal) else amount
    except (InvalidOperation, ValueError):
        safe_amount = None

    ctx = RiskContext(
        operation_type="TRANSFER",
        actor=actor,
        customer=source.customer,
        amount=safe_amount,
        currency=source.currency,
        account_ref=str(source.pk),
        beneficiary_id=beneficiary.pk if beneficiary else None,
        idempotency_key=idempotency_key,
    )
    try:
        return evaluate_operation(ctx, source_account=source, user=actor)
    except Exception as exc:  # fail-open with full evidence (INV 9)
        from apps.audit.services import record as audit

        audit(actor=actor, action="RISK_EVALUATION_ERROR", metadata={"error": str(exc)[:200]})
        return None


def _transfer_facts(source, amount, destination, beneficiary, key):
    """Material facts bound into the challenge hash. ANY change after
    issuance invalidates the challenge (INV 5)."""
    return {
        "amount": str(amount),
        "beneficiary": str(beneficiary.pk if beneficiary else ""),
        "source_account": str(source.pk),
        "destination_account": str(destination.pk if destination else ""),
        "idempotency_key": key,
    }


def resume_transfer(*, actor, challenge_id, code, facts, description=""):
    """Resume a STEP_UP_REQUIRED transfer with its exact original facts.

    Facts are NOT trusted: they travel through the client round-trip and
    are re-validated against the challenge's material hash inside the
    risk gate. Settlement happens at most once via the idempotency_key.
    """
    dest = str(facts.get("destination_account") or "")
    ben = str(facts.get("beneficiary") or "")
    return execute_transfer(
        actor=actor,
        source_account_id=int(facts["source_account"]),
        amount=facts["amount"],
        destination_account_id=int(dest) if dest.isdigit() else None,
        beneficiary_id=int(ben) if ben.isdigit() else None,
        description=description or "",
        idempotency_key=facts["idempotency_key"],
        step_up_code=code,
        step_up_challenge_id=challenge_id,
    )


def _risk_gate(actor, source, amount, destination, beneficiary, key,
               scheduled_for=None, recurrence="",
               step_up_code=None, step_up_challenge_id=None):
    """Limited-enforcement gate (spec PART 37): the engine decision now acts.

    Returns "ALLOW" or "REVIEW". Raises:
      STEP_UP_REQUIRED — effective CHALLENGE; a bound step-up challenge was issued.
      RISK_BLOCKED     — ENFORCEMENT BLOCK; a FAILED transfer row is recorded.
    SHADOW mode never interferes (effective_decision maps everything to ALLOW).
    Engine failure is fail-open with an audited trail.
    A presented step-up code satisfies a pending bound challenge exactly once
    (locked verify+consume); anything else still stops the flow.
    """
    from apps.fraud import modes

    ev = _risk_evaluation(actor, source, amount, destination, beneficiary, key)
    if ev is None:
        return "ALLOW"
    effective = modes.effective_decision(ev)

    if effective == "CHALLENGE":
        facts = _transfer_facts(source, amount, destination, beneficiary, key)
        if step_up_code and step_up_challenge_id:
            from apps.fraud.challenge import ChallengeError
            from apps.fraud.challenge_guard import confirm

            try:
                confirm(step_up_challenge_id, source.customer,
                        step_up_code, facts, f"TRANSFER:{key}",
                        actor=actor)
            except ChallengeError as exc:
                raise TransferError(str(exc), f"Step-up verification failed: {exc}")
            # verified & consumed → settlement proceeds below, once per key
        else:
            from apps.fraud.challenge_delivery import issue_and_deliver

            ch, _code = issue_and_deliver(ev, source.customer, facts, actor=actor)
            err = TransferError("STEP_UP_REQUIRED",
                                f"Step-up verification required (challenge {ch.pk})")
            err.challenge_id = ch.pk
            err.facts = facts   # canonical material facts for the confirm panel
            raise err

    raw = ev.decision
    enforcing = ev.engine_mode == "ENFORCEMENT"
    if raw == "BLOCK" and enforcing:
        t = Transfer.objects.create(
            reference=f"TRF-{uuid.uuid4().hex[:12].upper()}",
            idempotency_key=key,
            source_account=source,
            destination_account=destination,
            beneficiary=beneficiary,
            amount=amount,
            currency=source.currency,
            status=TransferStatus.FAILED,
            failure_reason=f"RISK_BLOCKED: {ev.triggered_rules}",
            created_by=actor,
            scheduled_for=scheduled_for,
            recurrence=recurrence,
        )
        from apps.audit.services import record as audit

        audit(actor=actor, action="TRANSFER_FAILED", resource=t,
              metadata={"reason": "RISK_BLOCKED", "ruleset_version": ev.ruleset_version})
        raise TransferError("RISK_BLOCKED", "Transfer blocked by risk policy")
    return "REVIEW" if (raw == "REVIEW" and enforcing) else "ALLOW"


def _post_gate_review_flag(source, key):
    """The pre-transaction gate already persisted the evaluation; look up
    whether it routed this operation to REVIEW."""
    from apps.fraud.models import RiskEvaluation

    ev = RiskEvaluation.objects.filter(idempotency_key=key).order_by("-pk").first()
    if ev is None:
        return False
    return (
        ev.engine_mode == "ENFORCEMENT"
        and ev.decision == RiskEvaluation.Decision.REVIEW
    )


def execute_transfer(*, actor, source_account_id, amount, destination_account_id=None,
                     beneficiary_id=None, description="", idempotency_key=None,
                     scheduled_for=None, recurrence="",
                     step_up_code=None, step_up_challenge_id=None):
    """Public entry: the risk gate runs OUTSIDE the settlement transaction so
    that block/challenge evidence survives an aborted transfer (INV 9/10)."""
    from apps.accounts.models import Account

    source = Account.objects.select_related("customer").get(pk=source_account_id)
    beneficiary = None
    if beneficiary_id:
        from apps.accounts.models import Beneficiary

        beneficiary = Beneficiary.objects.filter(pk=beneficiary_id).first()
    destination = (
        Account.objects.get(pk=destination_account_id) if destination_account_id else None
    )
    key = idempotency_key or str(uuid.uuid4())
    existing = Transfer.objects.filter(idempotency_key=key).first()
    if existing:
        return existing, False  # replays never re-enter the risk gate
    _risk_gate(actor, source, amount, destination, beneficiary, key,
               scheduled_for=scheduled_for, recurrence=recurrence,
               step_up_code=step_up_code,
               step_up_challenge_id=step_up_challenge_id)
    return _execute_transfer_atomic(
        actor=actor, source_account_id=source_account_id, amount=amount,
        destination_account_id=destination_account_id, beneficiary_id=beneficiary_id,
        description=description, idempotency_key=key,
        scheduled_for=scheduled_for, recurrence=recurrence,
    )


@transaction.atomic
def _execute_transfer_atomic(*, actor, source_account_id, amount, destination_account_id=None,
                             beneficiary_id=None, description="", idempotency_key=None,
                             scheduled_for=None, recurrence=""):
    """
    Execute an internal or external transfer atomically.

    Guarantees:
      - idempotent by idempotency_key (retries return the original result)
      - row-locked balance check prevents double spending
      - posts a balanced double-entry journal before commit
      - enforces limits, status, currency and fraud rules
    Returns (transfer, created).
    """
    key = idempotency_key or str(uuid.uuid4())
    existing = Transfer.objects.filter(idempotency_key=key).first()
    if existing:
        return existing, False

    amount = _validate_amount(amount)

    source = Account.objects.select_for_update().get(pk=source_account_id)
    if actor.is_customer and source.customer_id != actor.id:
        # object-level authorization: never reveal other accounts
        raise TransferError("FORBIDDEN", "You do not own this account")
    _require_active(source)
    if source.currency != "USD":
        raise TransferError("CURRENCY_MISMATCH", f"Unsupported currency {source.currency}")

    destination = None
    beneficiary = None
    if destination_account_id:
        destination = Account.objects.select_for_update().get(pk=destination_account_id)
        _require_active(destination)
        if destination.currency != source.currency:
            raise TransferError("CURRENCY_MISMATCH", "Currency mismatch between accounts")
    elif beneficiary_id:
        from apps.accounts.models import Beneficiary

        beneficiary = Beneficiary.objects.get(pk=beneficiary_id, owner=actor)
        if not beneficiary.verified:
            raise TransferError("BENEFICIARY_UNVERIFIED", "Beneficiary is not verified")
        if beneficiary.currency != source.currency:
            raise TransferError("CURRENCY_MISMATCH", "Currency mismatch with beneficiary")

    # limits
    if amount > source.tx_limit:
        raise TransferError("TX_LIMIT_EXCEEDED", f"Transaction limit is {source.tx_limit}")
    out_today = daily_outflow_total(source)
    if out_today + amount > source.daily_limit:
        raise TransferError("DAILY_LIMIT_EXCEEDED", f"Daily limit is {source.daily_limit}")

    # funds: locked-row projection from ledger
    available = source.available_balance
    if available < amount:
        raise TransferError("INSUFFICIENT_FUNDS", "Insufficient available funds")

    # fraud gate ran pre-transaction (see execute_transfer); its verdict arrives
    # via the idempotency-key-scoped evaluation — REVIEW routing only:
    risk_review = _post_gate_review_flag(source, key)

    status = TransferStatus.PENDING if scheduled_for else TransferStatus.CREATED
    if risk_review:
        status = TransferStatus.UNDER_REVIEW

    transfer = Transfer.objects.create(
        reference=f"TRF-{uuid.uuid4().hex[:12].upper()}",
        idempotency_key=key,
        source_account=source,
        destination_account=destination,
        beneficiary=beneficiary,
        amount=amount,
        currency=source.currency,
        description=description,
        status=status,
        created_by=actor,
        scheduled_for=scheduled_for,
        recurrence=recurrence,
    )

    if risk_review:
        audit(actor=actor, action="TRANSFER_CREATED", resource=transfer, metadata={"under_review": True})
        return transfer, True

    if transfer.status != TransferStatus.PENDING:
        _settle(transfer, actor)  # scheduled transfers wait for run_scheduled_jobs
        return transfer, True
    audit(actor=actor, action="TRANSFER_SCHEDULED", resource=transfer)
    return transfer, True


@transaction.atomic
def _settle(transfer: Transfer, actor):
    """Post the double-entry journal and complete the transfer. Caller holds locks? No — re-acquire."""
    src_ledger = transfer.source_account.ledger_account
    bank_clearing = ledger.get_or_create_account("1000-BANK-CLEARING", "Bank Clearing", type="ASSET")
    # Customer deposits are LIABILITIES of the bank: an outgoing transfer debits the
    # sender's liability ledger account.
    if transfer.destination_account_id:
        dst_ledger = transfer.destination_account.ledger_account
        lines = [
            (src_ledger, "DEBIT", transfer.amount),      # sender liability down
            (dst_ledger, "CREDIT", transfer.amount),     # receiver liability up
        ]
    else:
        lines = [
            (src_ledger, "DEBIT", transfer.amount),
            (bank_clearing, "CREDIT", transfer.amount),  # external settlement via clearing
        ]

    journal = ledger.post_journal(
        reference=transfer.reference,
        description=transfer.description or f"Transfer {transfer.reference}",
        lines=lines,
    )
    transfer.journal = journal
    transfer.save(update_fields=["journal"])
    transfer.transition(TransferStatus.PROCESSING)
    transfer.transition(TransferStatus.COMPLETED)
    audit(actor=actor, action="TRANSFER_COMPLETED", resource=transfer)


@transaction.atomic
def reverse_transfer(transfer, actor):
    if transfer.status != TransferStatus.COMPLETED:
        raise TransferError("NOT_REVERSIBLE", "Only completed transfers can be reversed")
    ledger.reverse_journal(transfer.journal, reference=f"REV-{transfer.reference}")
    transfer.transition(TransferStatus.REVERSED)
    audit(actor=actor, action="TRANSFER_REVERSED", resource=transfer)
    return transfer


@transaction.atomic
def approve_transfer(transfer, manager):
    if transfer.status != TransferStatus.UNDER_REVIEW:
        raise TransferError("NOT_PENDING_APPROVAL", "Transfer is not under review")
    transfer.approved_by = manager
    transfer.save(update_fields=["approved_by"])
    transfer.transition(TransferStatus.PROCESSING)
    transfer.refresh_from_db()
    _settle_locked(transfer, manager)


@transaction.atomic
def _settle_locked(transfer, actor):
    src = Account.objects.select_for_update().get(pk=transfer.source_account_id)
    if src.available_balance < transfer.amount:
        transfer.failure_reason = "INSUFFICIENT_FUNDS at approval"
        transfer.transition(TransferStatus.FAILED)
        return transfer
    _settle(transfer, actor)
    return transfer


@transaction.atomic
def process_due_scheduled(now=None):
    """Called by management command run_scheduled_jobs."""
    now = now or timezone.now()
    due = Transfer.objects.select_for_update().filter(
        status=TransferStatus.PENDING, scheduled_for__lte=now
    )
    processed = []
    for t in due:
        try:
            src = Account.objects.select_for_update().get(pk=t.source_account_id)
            _require_active(src)
            if src.available_balance < t.amount:
                t.failure_reason = "INSUFFICIENT_FUNDS"
                t.transition(TransferStatus.FAILED)
            else:
                _settle(t, t.created_by)
                processed.append(t.reference)
        except TransferError as e:
            t.failure_reason = e.code
            t.transition(TransferStatus.FAILED)
    return processed
