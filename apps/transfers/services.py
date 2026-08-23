"""Transfer domain service: the single entry point for money movement."""
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from apps.accounts.models import Account, AccountStatus
from apps.audit.services import record as audit
from apps.compliance.services import evaluate_fraud
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


@transaction.atomic
def execute_transfer(*, actor, source_account_id, amount, destination_account_id=None,
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

    # fraud rules may route to review / block
    verdict = evaluate_fraud(actor=actor, source=source, amount=amount, destination=destination, beneficiary=beneficiary)
    if verdict.blocked:
        t = Transfer.objects.create(
            reference=f"TRF-{uuid.uuid4().hex[:12].upper()}",
            idempotency_key=key,
            source_account=source,
            destination_account=destination,
            beneficiary=beneficiary,
            amount=amount,
            currency=source.currency,
            description=description,
            status=TransferStatus.FAILED,
            failure_reason=f"FRAUD_BLOCKED: {verdict.reason}",
            created_by=actor,
            scheduled_for=scheduled_for,
            recurrence=recurrence,
        )
        audit(actor=actor, action="TRANSFER_FAILED", resource=t, metadata={"reason": verdict.reason})
        raise TransferError("FRAUD_BLOCKED", verdict.reason)

    status = TransferStatus.PENDING if scheduled_for else TransferStatus.CREATED
    if verdict.review:
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

    if verdict.review:
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
