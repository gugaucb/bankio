"""Account opening service — risk-observed creation (spec PART 28)."""
import random

from django.db import transaction

from apps.fraud.context import RiskContext
from apps.ledger import services as ledger

from .models import Account, AccountType


class AccountOpeningError(Exception):
    pass


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
