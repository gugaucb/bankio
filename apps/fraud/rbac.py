"""Fraud role model (spec PART 14).

Segregation of duties (§51): fraud OPERATIONS (alerts/cases) are separate
from POLICY MANAGEMENT (rules/policies). Only FRAUD_MANAGER changes what
the engine enforces; analysts investigate.
"""
from functools import wraps

from apps.identity.models import Role

PERMISSIONS = {
    Role.FRAUD_ANALYST: {
        "view_alerts",
        "claim_case",
        "add_case_note",
        "request_customer_verification",
    },
    Role.SENIOR_FRAUD_ANALYST: {
        "view_alerts",
        "claim_case",
        "add_case_note",
        "request_customer_verification",
        "release_transaction",
        "confirm_fraud",
        "close_case",
    },
    Role.FRAUD_MANAGER: {
        "view_alerts",
        "claim_case",
        "add_case_note",
        "request_customer_verification",
        "release_transaction",
        "confirm_fraud",
        "close_case",
        "manage_rules",
        "manage_policies",
        "change_fraud_mode",
    },
}


class FraudPermissionDenied(PermissionError):
    pass


def has_permission(user, permission) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    allowed = PERMISSIONS.get(user.role, set())
    return permission in allowed


def require_permission(permission):
    def deco(fn):
        @wraps(fn)
        def wrapper(user, *args, **kwargs):
            if not has_permission(user, permission):
                raise FraudPermissionDenied(f"{permission} required")
            return fn(user, *args, **kwargs)

        return wrapper

    return deco
