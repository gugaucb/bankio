"""Fraud RBAC: segregation of duties between operations and policy."""
import pytest

from apps.fraud.rbac import (
    FraudPermissionDenied,
    has_permission,
    require_permission,
)


def _user_with_role(django_user_model, role):
    return django_user_model.objects.create_user(
        f"rbac-{role.lower()}", email=f"{role.lower()}@t.io", password="x", role=role,
    )


@pytest.mark.parametrize("role,perm,expected", [
    ("FRAUD_ANALYST", "view_alerts", True),
    ("FRAUD_ANALYST", "confirm_fraud", False),
    ("FRAUD_ANALYST", "manage_rules", False),          # SoD: analysts never touch rules
    ("SENIOR_FRAUD_ANALYST", "release_transaction", True),
    ("SENIOR_FRAUD_ANALYST", "manage_policies", False),
    ("FRAUD_MANAGER", "manage_rules", True),
    ("FRAUD_MANAGER", "manage_policies", True),
    ("CUSTOMER", "view_alerts", False),
    ("MANAGER", "manage_rules", False),                # bank managers are not fraud policy owners
])
def test_permission_matrix(django_user_model, db, role, perm, expected):
    user = _user_with_role(django_user_model, role)
    assert has_permission(user, perm) is expected


def test_anonymous_and_missing_role_denied(db, django_user_model):
    assert has_permission(None, "view_alerts") is False
    customer = django_user_model.objects.create_user("rbac-cust2", email="rc@t.io", password="x")
    assert has_permission(customer, "claim_case") is False


def test_decorator_enforces_and_passes_through(django_user_model, db):
    analyst = _user_with_role(django_user_model, "FRAUD_ANALYST")
    manager = _user_with_role(django_user_model, "FRAUD_MANAGER")

    @require_permission("manage_rules")
    def change_rule(actor, rule_id):
        return f"changed {rule_id}"

    with pytest.raises(FraudPermissionDenied):
        change_rule(analyst, "R1")
    assert change_rule(manager, "R1") == "changed R1"
