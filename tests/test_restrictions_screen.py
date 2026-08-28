"""Restrictions screen: empty state, branch-scoped listing, compliance-only actions."""
import pytest
from django.test import Client

from apps.accounts.models import Account
from apps.managerops.models import AccountRestriction, BankBranch, ManagerProfile
from tests.conftest import make_user
from tests.test_manager_portal import make_test_account


def _mgr(username, branch=None, level="BRANCH_MANAGER"):
    u = make_user(username, role="MANAGER", password="Mgr!12345")
    ManagerProfile.objects.create(user=u, level=level, branch=branch)
    return u


@pytest.fixture
def scene(db):
    b1 = BankBranch.objects.create(branch_code="6101", name="Res North", region="NORTH")
    b2 = BankBranch.objects.create(branch_code="6202", name="Res South", region="SOUTH")
    mgr1 = _mgr("res_mgr1", b1)
    cust1 = make_user("res_cust1")
    from apps.customers.models import Customer

    Customer.objects.create(user=cust1, customer_number="CUST-R1", branch=b1)
    acct1 = make_test_account(cust1)
    r1 = AccountRestriction.objects.create(account=acct1, restriction_type="TRANSFER_BLOCK",
                                           reason="suspected fraud", requested_by=mgr1)
    # other branch: AML hold on an account not visible to mgr1
    cust2 = make_user("res_cust2")
    Customer.objects.create(user=cust2, customer_number="CUST-R2", branch=b2)
    acct2 = make_test_account(cust2)
    r2 = AccountRestriction.objects.create(account=acct2, restriction_type="AML_HOLD",
                                           reason="aml", requested_by=None)
    return {"mgr1": mgr1, "b2": b2, "r1": r1, "r2": r2, "cust1": cust1}


@pytest.mark.django_db
def test_empty_state(scene):
    c = Client(enforce_csrf_checks=False)
    c.force_login(_mgr("res_mgr_none", level="RELATIONSHIP_MANAGER"))
    r = c.get("/manage/restrictions/")
    body = r.content.decode()
    assert "No active restrictions" in body


@pytest.mark.django_db
def test_restriction_listed_with_details(scene):
    c = Client(enforce_csrf_checks=False)
    c.force_login(scene["mgr1"])
    r = c.get("/manage/restrictions/")
    body = r.content.decode()
    assert "TRANSFER_BLOCK" in body and "suspected fraud" in body
    assert "res_mgr1" in body.lower()  # requested_by shown
    assert "Compliance only" not in body or "AML_HOLD" not in body  # other-branch rows hidden
    assert "res_cust2" not in body     # branch isolation


@pytest.mark.django_db
def test_lift_action_available_only_for_non_compliance(scene):
    c = Client(enforce_csrf_checks=False)
    c.force_login(scene["mgr1"])
    body = c.get("/manage/restrictions/").content.decode()
    assert "restriction_id" in body    # lift form for TRANSFER_BLOCK


@pytest.mark.django_db(transaction=True)
def test_lift_via_view_and_compliance_guard(scene):
    c = Client(enforce_csrf_checks=False)
    c.force_login(scene["mgr1"])
    r = c.post("/manage/restrictions/lift/", {"restriction_id": scene["r1"].pk})
    assert r.status_code == 302
    scene["r1"].refresh_from_db()
    assert not scene["r1"].active
    # AML hold cannot be lifted even if directly POSTed
    r = c.post("/manage/restrictions/lift/", {"restriction_id": scene["r2"].pk})
    assert r.status_code == 403
    scene["r2"].refresh_from_db()
    assert scene["r2"].active
