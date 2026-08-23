"""Sandbox investments: market orders settled from a cash account."""
import uuid
from decimal import Decimal

from django.db import transaction

from apps.accounts.models import AccountStatus
from apps.ledger import services as ledger

from .models import Instrument, Order, Position


class TradingError(Exception):
    pass


@transaction.atomic
def place_order(*, actor, account_id, symbol, side, quantity, idempotency_key=None):
    key = idempotency_key or str(uuid.uuid4())
    existing = Order.objects.filter(idempotency_key=key).first()
    if existing:
        return existing, False

    try:
        instrument = Instrument.objects.get(symbol=symbol.upper())
    except Instrument.DoesNotExist:
        raise TradingError("UNKNOWN_INSTRUMENT")

    quantity = Decimal(str(quantity))
    if quantity <= 0:
        raise TradingError("INVALID_QUANTITY")

    from apps.accounts.models import Account

    account = Account.objects.select_for_update().get(pk=account_id)
    if actor.is_customer and account.customer_id != actor.id:
        raise TradingError("FORBIDDEN")
    if account.status != AccountStatus.ACTIVE:
        raise TradingError("ACCOUNT_NOT_ACTIVE")

    notional = (quantity * instrument.last_price).quantize(Decimal("0.01"))
    broker_asset = ledger.get_or_create_account("3200-BROKER-CLEARING", "Broker Clearing", type="ASSET")
    journal = None
    if side == "BUY":
        if account.available_balance < notional:
            raise TradingError("INSUFFICIENT_FUNDS")
        journal = ledger.post_journal(
            reference=f"ORD-{uuid.uuid4().hex[:10].upper()}",
            description=f"BUY {quantity} {instrument.symbol}",
            lines=[(account.ledger_account, "DEBIT", notional), (broker_asset, "CREDIT", notional)],
        )
        pos, _ = Position.objects.get_or_create(customer=actor, instrument=instrument)
        total_cost = pos.quantity * pos.avg_price + notional
        pos.quantity += quantity
        pos.avg_price = (total_cost / pos.quantity).quantize(Decimal("0.01"))
        pos.save()
    else:
        pos = Position.objects.filter(customer=actor, instrument=instrument).first()
        if not pos or pos.quantity < quantity:
            raise TradingError("INSUFFICIENT_POSITION")
        journal = ledger.post_journal(
            reference=f"ORD-{uuid.uuid4().hex[:10].upper()}",
            description=f"SELL {quantity} {instrument.symbol}",
            lines=[(broker_asset, "DEBIT", notional), (account.ledger_account, "CREDIT", notional)],
        )
        pos.quantity -= quantity
        pos.save()

    order = Order.objects.create(
        idempotency_key=key, customer=actor, instrument=instrument,
        side=side, quantity=quantity, price=instrument.last_price, journal=journal,
    )
    return order, True
