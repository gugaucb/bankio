"""Account opening + funding services (all money movement goes through the ledger)."""
import random
import secrets
from decimal import Decimal

from django.db import transaction

from apps.fraud.context import RiskContext
from apps.ledger import services as ledger

from .models import Account, AccountStatus, AccountType


class AccountOpeningError(Exception):
    pass


class FundingError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


SYSTEM_FUNDING_CODE = "9100-SYSTEM-FUNDING"


def _funding_source(currency):
    """Simulated external inflow source (e.g. cash deposit at branch / ACH in)."""
    return ledger.get_or_create_account(
        SYSTEM_FUNDING_CODE, "System Funding Source", type="ASSET", currency=currency,
    )


@transaction.atomic
def fund_account(*, manager, account_id, amount, reason="", external_ref="",
                 idempotency_key="") -> dict:
    """Credit a customer account through the ledger (double-entry, idempotent).

    Balance is derived from posted journal entries — never mutated directly.
    Raises FundingError on invalid input; safe on replay (same idempotency key
    returns the original journal without creating money).
    """
    from apps.audit.services import record as audit

    existing = ledger.find_idempotent(idempotency_key) if idempotency_key else None
    if existing is not None:
        return {"journal": existing.journal, "replayed": True, "account_id": None}

    try:
        amount = Decimal(str(amount))
    except Exception:
        raise FundingError("INVALID_AMOUNT", "Funding amount must be a number")
    if amount <= 0:
        raise FundingError("INVALID_AMOUNT", "Funding amount must be positive")

    account = Account.objects.select_for_update().filter(pk=account_id).first()
    if account is None:
        raise FundingError("ACCOUNT_NOT_FOUND")
    if account.status != AccountStatus.ACTIVE:
        raise FundingError("ACCOUNT_NOT_ACTIVE")
    reference = f"FUND-{secrets.token_hex(6).upper()}"
    journal = ledger.post_journal(
        reference=reference,
        description=reason or f"Account funding {account.account_number}",
        lines=[
            (_funding_source(account.currency), "DEBIT", amount),
            (account.ledger_account, "CREDIT", amount),
        ],
        currency=account.currency,
    )
    ledger.record_idempotent(idempotency_key or reference, "FUNDING", journal,
                             {"account": account.account_number, "amount": str(amount)})
    audit(actor=manager, action="FUNDING_EXECUTED", resource=account,
          metadata={"reference": reference, "amount": str(amount),
                    "external_ref": external_ref[:60], "reason": reason[:120]})
    from apps.notifications.services import notify

    notify(recipient=account.customer, category="ACCOUNT", kind="DEPOSIT",
           title="Deposit credited",
           body=f"{amount} {account.currency} was credited to account {account.account_number}.",
           metadata={"reference": reference, "amount": str(amount)},
           dedup_key=f"FUNDING:{reference}")
    return {"journal": journal, "replayed": False, "account": account, "amount": amount}


def _risk_observation(customer):
    """Shadow risk observation on the opening; never fatal in SHADOW."""
    from apps.fraud.engine import evaluate_operation
    from apps.audit.services import record as audit

    ctx = RiskContext(operation_type="ACCOUNT_OPENING", actor=customer, customer=customer)
    try:
        return evaluate_operation(ctx)
    except Exception as exc:
        audit(action="RISK_EVALUATION_ERROR", actor=customer,
              metadata={"scope": "account_opening", "error": str(exc)[:200]})
        return None


@transaction.atomic
def open_account(*, customer, type=AccountType.CHECKING, currency="USD"):
    """Create a customer account with its ledger pair; shadow risk first."""
    _risk_observation(customer)
    number = f"4000{random.randint(10**10, 10**11 - 1)}"
    la = ledger.get_or_create_account(f"2001-{number}", f"Customer {number}", is_customer=True)
    return Account.objects.create(
        customer=customer, account_number=number, type=type,
        currency=currency, ledger_account=la,
    )
