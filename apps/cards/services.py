"""Card domain services: controls, purchases, statement payments."""
import uuid
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import Account, AccountStatus
from apps.audit.services import record as audit
from apps.ledger import services as ledger

from .models import Card, CardRequest, CardStatus, CardTransaction, CreditStatement


class CardDeclined(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _assert_owner(card, actor):
    if not getattr(actor, "is_bank_staff", False) and card.account.customer_id != actor.id:
        raise PermissionDenied("Not your card")


@transaction.atomic
def set_card_control(actor, card_id, **controls):
    """freeze/unfreeze/limits/toggles. Valid keys mirror Card boolean/limit fields + status."""
    allowed = {
        "online_enabled", "international_enabled", "contactless_enabled",
        "atm_enabled", "tx_limit", "daily_limit",
    }
    card = Card.objects.select_for_update().get(pk=card_id)
    _assert_owner(card, actor)
    if card.status == CardStatus.BLOCKED:
        raise CardDeclined("CARD_BLOCKED")

    changed = []
    if "status" in controls:
        new_status = controls.pop("status")
        if new_status in (CardStatus.FROZEN, CardStatus.ACTIVE):
            card.status = new_status
            changed.append(f"status={new_status}")
    for key, value in controls.items():
        if key not in allowed:
            raise ValueError(f"Unknown control {key}")
        setattr(card, key, value)
        changed.append(f"{key}={value}")
    card.clean_limits()
    card.save()
    audit(actor=actor, action="CARD_UPDATED", resource=card, metadata={"changes": changed})
    return card


def freeze_card(actor, card_id):
    return set_card_control(actor, card_id, status=CardStatus.FROZEN)


def unfreeze_card(actor, card_id):
    return set_card_control(actor, card_id, status=CardStatus.ACTIVE)


def report_lost_or_stolen(actor, card_id, stolen=False):
    card = set_card_control(actor, card_id, status=CardStatus.BLOCKED)
    audit(actor=actor, action="CARD_REPORTED_LOST" if not stolen else "CARD_REPORTED_STOLEN", resource=card)
    return card


@transaction.atomic
def purchase(card_id, merchant, amount_raw, online=False, international=False, atm=False, idempotency_key=None):
    """Simulated acquirer request. Declines enforce card state and limits; posts ledger."""
    amount = Decimal(str(amount_raw)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise CardDeclined("INVALID_AMOUNT")
    key = f"card-purchase:{idempotency_key}" if idempotency_key else None
    card = Card.objects.select_for_update().select_related("account__ledger_account").get(pk=card_id)
    # idempotency replay must be checked only AFTER taking the card lock,
    # so a concurrent retry waits for the first attempt to commit
    existing = ledger.find_idempotent(key)
    if existing:
        return CardTransaction.objects.get(pk=existing.result["tx_id"])

    def decline(reason):
        CardTransaction.objects.create(
            card=card, merchant=merchant, amount=amount,
            international=international, online=online,
            declined=True, decline_reason=reason,
        )
        raise CardDeclined(reason)

    if card.is_expired:
        decline("EXPIRED")
    if card.status == CardStatus.FROZEN:
        decline("FROZEN")
    if card.status != CardStatus.ACTIVE:
        decline(f"CARD_{card.status}")
    if online and not card.online_enabled:
        decline("ONLINE_DISABLED")
    if international and not card.international_enabled:
        decline("INTERNATIONAL_DISABLED")
    if amount > card.tx_limit:
        decline("TX_LIMIT_EXCEEDED")
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    spent = (
        CardTransaction.objects.filter(card=card, declined=False, created_at__gte=today_start)
        .aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )
    if spent + amount > card.daily_limit:
        decline("DAILY_LIMIT_EXCEEDED")

    # full enforcement (spec PART 39): engine decisions now decline purchases.
    # Hard controls above remain decisive regardless of score (spec PART 21).
    ev = _card_risk_observation(card, merchant, amount, online, international)
    if ev is not None:
        from apps.fraud.gate import RiskGateIntervention, enforce

        try:
            enforce(ev)
        except RiskGateIntervention as g:
            decline(g.action)  # records a declined CardTransaction row

    account = card.account
    if card.type == "DEBIT_CARD":
        if account.status != AccountStatus.ACTIVE:
            decline("ACCOUNT_NOT_ACTIVE")
        if account.available_balance < amount:
            decline("INSUFFICIENT_FUNDS")
    else:  # credit-type cards draw against credit limit
        used = sum(t.amount for t in card.transactions.filter(declined=False)) - sum(
            s.amount_due for s in card.statements.filter(paid=True)
        )
        if used + amount > card.credit_limit:
            decline("CREDIT_LIMIT_EXCEEDED")

    # post to ledger: debit expense (bank receivable), credit customer liability (funds leave)
    bank_receivable = ledger.get_or_create_account("3000-CARD-RECEIVABLE", "Card Acquirer Receivable", type="ASSET")
    lines = [
        (account.ledger_account, "DEBIT", amount),
        (bank_receivable, "CREDIT", amount),
    ]
    journal = ledger.post_journal(reference=f"CDT-{uuid.uuid4().hex[:12].upper()}", description=f"Card purchase {merchant}", lines=lines)
    tx = CardTransaction.objects.create(
        card=card, merchant=merchant, amount=amount,
        international=international, online=online, journal=journal,
    )
    ledger.record_idempotent(key, "CARD_PURCHASE", journal, {"tx_id": tx.pk})
    return tx


@transaction.atomic
def pay_statement(actor, card_id, statement_id=None, idempotency_key=None):
    """Pay outstanding credit-card statement from the linked account."""
    key = f"stmt-pay:{idempotency_key}" if idempotency_key else None
    card = Card.objects.select_for_update().select_related("account__ledger_account").get(pk=card_id)
    existing = ledger.find_idempotent(key)
    if existing:
        return Decimal(str(existing.result["total"]))
    _assert_owner(card, actor)
    stmts = CreditStatement.objects.select_for_update().filter(card=card, paid=False).order_by("period_end")
    if statement_id:
        stmts = stmts.filter(pk=statement_id)
    total = sum((s.amount_due for s in stmts), Decimal("0"))
    if not stmts or total <= 0:
        raise CardDeclined("NO_OUTSTANDING_STATEMENT")
    account = card.account
    if account.available_balance < total:
        raise CardDeclined("INSUFFICIENT_FUNDS")
    receivable = ledger.get_or_create_account("3000-CARD-RECEIVABLE", "Card Acquirer Receivable", type="ASSET")
    journal = ledger.post_journal(
        reference=f"STMT-{uuid.uuid4().hex[:12].upper()}",
        description=f"Statement payment card {card.last4}",
        lines=[
            (account.ledger_account, "DEBIT", total),  # reduce customer liability (funds out)
            (receivable, "CREDIT", total),             # settle card receivable
        ],
    )
    now = timezone.now()
    stmts.update(paid=True, paid_at=now)
    ledger.record_idempotent(key, "CARD_STATEMENT_PAYMENT", journal, {"total": str(total)})
    audit(actor=actor, action="CARD_STATEMENT_PAID", resource=card, metadata={"amount": str(total), "journal": journal.reference})
    return total


# ---------------------------------------------------------------- card requests
MAX_REQUESTED_LIMIT = Decimal("20000.00")


class CardRequestError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@transaction.atomic
def request_card(customer, account_id, card_type="CREDIT_CARD", requested_limit=None) -> CardRequest:
    """Customer applies for a card on one of their active accounts."""
    from decimal import Decimal as D

    limit = D(str(requested_limit or 2000)).quantize(D("0.01"))
    if limit <= 0 or limit > MAX_REQUESTED_LIMIT:
        raise CardRequestError("INVALID_LIMIT")
    account = Account.objects.get(pk=account_id, customer=customer)
    if account.status != AccountStatus.ACTIVE:
        raise CardRequestError("ACCOUNT_NOT_ACTIVE")
    if CardRequest.objects.filter(customer=customer, account=account, type=card_type,
                                  status=CardRequest.Status.PENDING).exists():
        raise CardRequestError("REQUEST_ALREADY_PENDING")
    req = CardRequest.objects.create(
        customer=customer, account=account, type=card_type,
        requested_limit=min(limit, MAX_REQUESTED_LIMIT),
    )
    audit(actor=customer, action="CARD_REQUESTED", resource=req,
          metadata={"type": card_type, "limit": str(limit)})
    return req


@transaction.atomic
def decide_card_request(req, approver, approve: bool, approved_limit=None, reason=""):
    """Manager decision. Approval issues the physical/virtual card with correct holder data."""
    if not getattr(approver, "is_bank_staff", False) or approver.role != "MANAGER":
        raise PermissionDenied("Only managers can decide card requests")
    req = CardRequest.objects.select_for_update().get(pk=req.pk)
    if req.status != CardRequest.Status.PENDING:
        raise CardRequestError("NOT_PENDING")
    if approve:
        if req.type == "CREDIT_CARD":
            limit = Decimal(str(approved_limit or req.requested_limit)).quantize(Decimal("0.01"))
            if limit <= 0 or limit > MAX_REQUESTED_LIMIT:
                raise CardRequestError("INVALID_LIMIT")
            req.approved_limit = limit
        else:
            limit = Decimal("0")
        full_name = req.customer.get_full_name().strip() or req.customer.username
        card = Card.objects.create(
            account=req.account,
            type=req.type,
            status=CardStatus.ACTIVE,
            holder_name=full_name.upper(),
            credit_limit=limit,
        )
        req.status = CardRequest.Status.APPROVED
        audit(actor=approver, action="CARD_REQUEST_APPROVED", resource=req,
              metadata={"card": card.masked_number, "holder": card.holder_name, "limit": str(limit)})
    else:
        req.status = CardRequest.Status.REJECTED
        req.decision_reason = reason or "Request declined."
        audit(actor=approver, action="CARD_REQUEST_REJECTED", resource=req)
    req.reviewed_by = approver
    req.decided_at = timezone.now()
    req.save()
    return req


def _card_risk_observation(card, merchant, amount, online, international):
    """Run the fraud engine in observation mode; never declines a purchase.
    Engine errors are audited and non-fatal — card business controls above
    are the authoritative decline path."""
    from apps.fraud.context import RiskContext
    from apps.fraud.engine import evaluate_operation

    ctx = RiskContext(
        operation_type="CARD_PURCHASE",
        actor=card.account.customer,
        customer=card.account.customer,
        amount=amount,
        currency="USD",
        account_ref=str(card.pk),
        idempotency_key=f"card:{card.pk}:{merchant}",
    )
    try:
        return evaluate_operation(ctx, card=card)
    except Exception as exc:
        from apps.audit.services import record as audit

        audit(action="RISK_EVALUATION_ERROR", metadata={"scope": "card_purchase", "error": str(exc)[:200]})
        return None

