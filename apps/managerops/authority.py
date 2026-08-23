"""Centralized authority policy: what each manager level may do/approve, and thresholds."""
from dataclasses import dataclass
from decimal import Decimal

from apps.managerops.models import LEVEL_ORDER, ManagerLevel

# operation -> {level: max amount they may approve directly}
THRESHOLDS = {
    "LIMIT_INCREASE": {1: Decimal("10000"), 2: Decimal("50000"), 3: Decimal("250000"), 4: Decimal("1000000")},
    "CREDIT_CARD_LIMIT": {1: Decimal("5000"), 2: Decimal("25000"), 3: Decimal("100000"), 4: Decimal("500000")},
    "LOAN_APPROVAL": {1: None, 2: Decimal("100000"), 3: Decimal("500000"), 4: Decimal("2000000")},
    "FEE_WAIVER": {1: Decimal("100"), 2: Decimal("500"), 3: Decimal("2000"), 4: Decimal("5000")},
    "ACCOUNT_OPENING": {1: Decimal("0"), 2: Decimal("250000"), 3: Decimal("1000000"), 4: Decimal("10000000")},
    # high-value account = opening balance expectation; >0 requires >= branch manager
    "ACCOUNT_UNBLOCK": {2: Decimal("0"), 3: Decimal("0"), 4: Decimal("0")},  # not AML/legal
    "OVERDRAFT": {2: Decimal("25000"), 3: Decimal("100000"), 4: Decimal("500000")},
    "RATE_EXCEPTION": {2: Decimal("0"), 3: Decimal("0"), 4: Decimal("0")},
    "ADJUSTMENT_REQUEST": {3: Decimal("10000"), 4: Decimal("100000")},
    "HIGH_RISK_CUSTOMER": {3: Decimal("0"), 4: Decimal("0")},
    "ACCOUNT_CLOSURE": {1: Decimal("0"), 2: Decimal("0"), 3: Decimal("0"), 4: Decimal("0")},
    "ONBOARDING_REVIEW": {1: Decimal("0"), 2: Decimal("0"), 3: Decimal("0"), 4: Decimal("0")},
}


class AuthorityError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def authority_limit(manager_profile, operation) -> Decimal | None:
    """Max amount this manager may approve for the operation. None = never allowed."""
    table = THRESHOLDS.get(operation)
    if not table:
        return None
    return table.get(manager_profile.rank)


@dataclass
class Decision:
    allowed: bool
    required_level: str | None = None
    reason: str = ""


def can_approve(manager_profile, operation, amount=None) -> Decision:
    limit = authority_limit(manager_profile, operation)
    if limit is None:
        return Decision(False, None, "OPERATION_NOT_PERMITTED_FOR_ROLE")
    if amount is None:
        return Decision(True)
    if amount <= limit:
        return Decision(True)
    # find minimum level that covers the amount
    table = THRESHOLDS[operation]
    for rank in sorted(table):
        if table[rank] is not None and amount <= table[rank]:
            required = next(l for l, r in LEVEL_ORDER.items() if r == rank)
            return Decision(False, required, "REQUIRES_HIGHER_APPROVAL")
    return Decision(False, ManagerLevel.REGIONAL, "EXCEEDS_BANK_AUTHORITY")
