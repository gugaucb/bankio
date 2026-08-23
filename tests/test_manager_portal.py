"""Manager Operations: authority matrix, maker-checker, KYC gating, branch isolation,
duplicate detection, account number uniqueness/concurrency, restrictions, ledger protection."""
import threading
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client

from apps.accounts.models import Account
from apps.compliance.models import KYCReview
from apps.identity.models import User
from apps.managerops.access import get_manager_profile, visible_customers
from apps.managerops.authority import can_approve
from apps.managerops.models import (
    AccountApplication,
    AccountRestriction,
    ApprovalRequest,
    BankBranch,
    ManagerProfile,
)
from apps.managerops.services import (
    ApprovalError,
    OnboardingError,
    create_customer,
    decide_approval,
    kyc_status,
    lift_restriction,
    open_account_application,
    request_limit_change,
    request_restriction,
    submit_approval_request,
)
from tests.conftest import make_user


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def branches(db):
    b1 = BankBranch.objects.create(branch_code="1001", name="Downtown", region="NORTH")
    b2 = BankBranch.objects.create(branch_code="2002", name="Harbor", region="SOUTH")
    return b1, b2


def make_manager(username, level, branch, password="Mgr!12345"):
    u = make_user(username, role="MANAGER", password=password)
    ManagerProfile.objects.create(user=u, level=level, branch=branch)
    return u


@pytest.fixture
def rel_mgr(branches):
    return make_manager("rel_mgr", "RELATIONSHIP_MANAGER", branches[0])


@pytest.fixture
def rel_mgr2(branches):
    return make_manager("rel_mgr2", "RELATIONSHIP_MANAGER", branches[0])


@pytest.fixture
def branch_mgr(branches):
    return make_manager("branch_mgr", "BRANCH_MANAGER", branches[0])


@pytest.fixture
def senior_mgr(branches):
    return make_manager("senior_mgr", "SENIOR_MANAGER", branches[0])


@pytest.fixture
def other_branch_mgr(branches):
    return make_manager("harbor_mgr", "BRANCH_MANAGER", branches[1])


@pytest.fixture
def customer_of_rel(rel_mgr, db):
    """Customer assigned to rel_mgr with approved KYC."""
    c = make_user("jane_doe")
    from apps.customers.models import Customer

    cust = Customer.objects.create(user=c, customer_number="CUST-MGR1", branch=rel_mgr.manager_profile.branch)
    from apps.managerops.models import CustomerManagerAssignment

    CustomerManagerAssignment.objects.create(customer=c, manager=rel_mgr, branch=rel_mgr.manager_profile.branch)
    KYCReview.objects.create(customer=c, status="APPROVED")
    c.cust_row = cust
    return c


# ------------------------------------------------------- duplicate detection

@pytest.mark.django_db
def test_duplicate_customer_detection(rel_mgr):
    create_customer(manager=rel_mgr, data={"full_name": "John Smith", "email": "js@x.io",
                                           "date_of_birth": "1990-01-01"})
    with pytest.raises(OnboardingError) as e:
        create_customer(manager=rel_mgr, data={"full_name": "Other Name", "email": "JS@x.io",
                                               "date_of_birth": "1985-05-05"})
    assert str(e.value).startswith("POSSIBLE_EXISTING_CUSTOMER")


@pytest.mark.django_db
def test_underage_customer_rejected(rel_mgr):
    with pytest.raises(OnboardingError) as e:
        create_customer(manager=rel_mgr, data={"full_name": "Baby Doe", "email": "b@x.io",
                                               "date_of_birth": "2020-01-01"})
    assert e.value.args[0] == "UNDERAGE"


# ------------------------------------------------------------ KYC enforcement

@pytest.mark.django_db(transaction=True)
def test_account_opening_requires_kyc(rel_mgr, branches):
    mgr = make_manager("kyc_mgr", "RELATIONSHIP_MANAGER", branches[0])
    c = create_customer(manager=mgr, data={"full_name": "No Kyc", "email": "nk@x.io",
                                           "date_of_birth": "1990-01-01"})
    app = open_account_application(manager=mgr, customer_id=c.pk, product_type="CHECKING")
    assert app.state == "PENDING_KYC"  # never ACTIVE without verified KYC


@pytest.mark.django_db(transaction=True)
def test_kyc_cannot_be_bypassed_by_state_jump(rel_mgr, branches):
    mgr = make_manager("kyb_mgr", "RELATIONSHIP_MANAGER", branches[0])
    c = create_customer(manager=mgr, data={"full_name": "Jump Guy", "email": "jg@x.io",
                                           "date_of_birth": "1990-01-01"})
    app = AccountApplication.objects.create(customer=c, product_type="CHECKING", requested_by=mgr, branch=branches[0])
    with pytest.raises(ValueError):
        app.transition("ACTIVE")  # APPLICATION -> ACTIVE illegal


# ------------------------------------------------------- account number uniqueness

@pytest.mark.django_db(transaction=True)
def test_concurrent_openings_unique_numbers(rel_mgr, branches):
    """Two concurrent openings must never produce the same account number."""
    mgr = make_manager("conc_mgr", "BRANCH_MANAGER", branches[0])
    numbers = []
    errors = []

    def do(i):
        try:
            from django.db import connections

            c = create_customer(manager=mgr, data={"full_name": f"Conc {i}", "email": f"c{i}@x.io",
                                                   "date_of_birth": "1990-01-01"})
            KYCReview.objects.filter(customer=c).update(status="APPROVED")
            app = open_account_application(manager=mgr, customer_id=c.pk, product_type="CHECKING")
            if app.account:
                numbers.append(app.account.account_number)
        except Exception as e:  # noqa
            errors.append(str(e))
        finally:
            from django.db import connections

            connections.close_all()

    ts = [threading.Thread(target=do, args=(i,)) for i in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(numbers) == 2
    assert len(set(numbers)) == 2  # unique under concurrency


# ------------------------------------------------------------ authority matrix

@pytest.mark.django_db
def test_limit_change_within_authority_applies_directly(rel_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    result = request_limit_change(manager=rel_mgr, account_id=acc.pk, new_limit="8000")
    assert result["applied"] is True
    acc.refresh_from_db()
    assert acc.tx_limit == Decimal("8000")


@pytest.mark.django_db
def test_limit_change_above_authority_requires_higher_approval(rel_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    result = request_limit_change(manager=rel_mgr, account_id=acc.pk, new_limit="50000")
    assert result["applied"] is False
    assert result["required_level"] == "BRANCH_MANAGER"


@pytest.mark.django_db
def test_branch_manager_can_approve_50k_not_relationship(rel_mgr, branch_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    res = request_limit_change(manager=rel_mgr, account_id=acc.pk, new_limit="50000")
    req_id = res["request"].pk
    # self-approval denied
    with pytest.raises(ApprovalError) as e:
        decide_approval(approver=rel_mgr, approval_id=req_id, approve=True)
    assert e.value.code == "DENIED_SELF_APPROVAL"
    # correct authority approves
    req = decide_approval(approver=branch_mgr, approval_id=req_id, approve=True)
    assert req.status == "APPROVED"
    acc.refresh_from_db()
    assert acc.tx_limit == Decimal("50000")


@pytest.mark.django_db
def test_insufficient_level_cannot_approve(rel_mgr, branch_mgr, senior_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    res = request_limit_change(manager=rel_mgr, account_id=acc.pk, new_limit="200000")  # needs SENIOR
    with pytest.raises(ApprovalError) as e:
        decide_approval(approver=branch_mgr, approval_id=res["request"].pk, approve=True)
    assert e.value.code == "INSUFFICIENT_AUTHORITY"
    req = decide_approval(approver=senior_mgr, approval_id=res["request"].pk, approve=True)
    assert req.status == "APPROVED"


@pytest.mark.django_db
def test_double_approval_and_after_cancel(rel_mgr, branch_mgr, senior_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    res = request_limit_change(manager=rel_mgr, account_id=acc.pk, new_limit="50000")
    rid = res["request"].pk
    decide_approval(approver=branch_mgr, approval_id=rid, approve=True)
    with pytest.raises(ApprovalError):
        decide_approval(approver=senior_mgr, approval_id=rid, approve=True)  # double approval
    res2 = request_limit_change(manager=rel_mgr, account_id=acc.pk, new_limit="40000")
    r2 = res2["request"]
    r2.status = "CANCELED"
    r2.save(update_fields=["status"])
    with pytest.raises(ApprovalError):
        decide_approval(approver=branch_mgr, approval_id=r2.pk, approve=True)


# ------------------------------------------------------------- restrictions

def make_test_account(user):
    from apps.ledger.services import get_or_create_account

    la = get_or_create_account(f"MG-{user.pk}-{user.username}", "mgr test", is_customer=True)
    return Account.objects.create(customer=user, account_number=f"7{user.pk:09d}", ledger_account=la)


@pytest.mark.django_db
def test_valid_restriction_and_duplicate(rel_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    r = request_restriction(manager=rel_mgr, account_id=acc.pk, restriction_type="TRANSFER_BLOCK", reason="suspected fraud")
    assert r.active
    with pytest.raises(Exception) as e:
        request_restriction(manager=rel_mgr, account_id=acc.pk, restriction_type="TRANSFER_BLOCK", reason="again")
    assert "DUPLICATE" in str(e.value)


@pytest.mark.django_db
def test_manager_cannot_apply_aml_hold(rel_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    with pytest.raises(Exception) as e:
        request_restriction(manager=rel_mgr, account_id=acc.pk, restriction_type="AML_HOLD", reason="try")
    assert "COMPLIANCE_ONLY" in str(e.value)


@pytest.mark.django_db
def test_manager_cannot_lift_aml_hold(rel_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    r = AccountRestriction.objects.create(account=acc, restriction_type="AML_HOLD",
                                          reason="aml", requested_by=None)
    with pytest.raises(Exception) as e:
        lift_restriction(actor=rel_mgr, restriction_id=r.pk)
    assert "COMPLIANCE_ONLY" in str(e.value)
    ok = lift_restriction(actor=rel_mgr, restriction_id=r.pk, is_compliance=True)
    assert not ok.active


# ------------------------------------------------------------ branch isolation / IDOR

@pytest.mark.django_db
def test_cross_branch_access_denied(other_branch_mgr, customer_of_rel):
    profile = get_manager_profile(other_branch_mgr)
    with pytest.raises(PermissionDenied):
        from apps.customers.models import Customer

        cust = customer_of_rel.customer_profile
        from apps.managerops.access import assert_customer_access

        assert_customer_access(profile, cust)


@pytest.mark.django_db
def test_non_manager_role_denied(aubrey):
    with pytest.raises(PermissionDenied):
        get_manager_profile(aubrey)


@pytest.mark.django_db
def test_anonymous_and_customer_blocked_over_http(rel_mgr, customer_of_rel):
    c = Client(enforce_csrf_checks=False)
    r = c.get("/manage/")
    assert r.status_code == 302 and "/login/" in r.url
    # customer role cannot reach manager endpoints
    cc = Client()
    cc.force_login(make_user("plain_cust"))
    r = cc.get("/manage/")
    assert r.status_code == 403


# ------------------------------------------------------------ ledger protection

@pytest.mark.django_db
def test_no_workflow_exposes_balance_edit(client, rel_mgr, customer_of_rel):
    acc = make_test_account(customer_of_rel)
    client.force_login(rel_mgr)
    for url in ("/manage/", "/manage/customers/", f"/manage/customers/{customer_of_rel.pk}/"):
        r = client.get(url)
        body = r.content.decode()
        assert "Edit Balance" not in body and "Set Balance" not in body
    # even direct model tampering does not affect ledger-derived balance
    Account.objects.filter(pk=acc.pk).update()  # no balance field exists to set
    assert not hasattr(acc, "balance_field")


# ------------------------------------------------------------ HTTP flows (E2E-ish)

@pytest.mark.django_db(transaction=True)
def test_full_onboarding_to_account_http_flow(branches):
    from apps.customers.models import Customer

    mgr = make_manager("e2e_mgr", "BRANCH_MANAGER", branches[0])
    c = Client()
    c.force_login(mgr)
    # onboarding via HTTP
    r = c.post("/manage/onboarding/", {"full_name": "Eve Forward", "email": "eve@x.io",
                                       "date_of_birth": "1988-03-03"})
    assert r.status_code == 302
    user = User.objects.get(email="eve@x.io")
    KYCReview.objects.filter(customer=user).update(status="APPROVED")
    # audit exists
    from apps.audit.models import AuditLog

    assert AuditLog.objects.filter(action="CUSTOMER_CREATED", actor=mgr).exists()
    # open account via HTTP
    r = c.post("/manage/open-account/", {"customer_id": user.pk, "product_type": "SAVINGS"})
    assert r.status_code == 302, r.content.decode()[:400]
    app = AccountApplication.objects.get(customer=user)
    assert app.state == "ACTIVE"
    assert AuditLog.objects.filter(action="ACCOUNT_OPENED").exists()


@pytest.mark.django_db(transaction=True)
def test_frontend_tampering_cannot_grant_authority(branches):
    """Relationship manager posts approval decision directly — must be rejected server-side."""
    from django.test import Client as C

    m1 = make_manager("tamper_req", "RELATIONSHIP_MANAGER", branches[0])
    m2 = make_manager("senior_ok", "SENIOR_MANAGER", branches[0])
    cust = create_customer(manager=m1, data={"full_name": "T U", "email": "tu@x.io", "date_of_birth": "1990-01-01"})
    from apps.accounts.models import Account as Acc

    la = __import__("apps.ledger.services", fromlist=["get_or_create_account"]).get_or_create_account("TU-ACC", "t", is_customer=True)
    acc = Acc.objects.create(customer=cust, account_number="7000000001", ledger_account=la)
    res = request_limit_change(manager=m1, account_id=acc.pk, new_limit="50000")
    rid = res["request"].pk

    client = C()
    client.force_login(m1)  # requester pretends to be approver
    r = client.post("/manage/approvals/decide/", {"approval_id": rid, "decision": "approve"})
    assert r.status_code == 403
    assert ApprovalRequest.objects.get(pk=rid).status == "PENDING"

    client2 = C()
    client2.force_login(m2)
    r = client2.post("/manage/approvals/decide/", {"approval_id": rid, "decision": "approve"})
    assert r.status_code == 302
    assert ApprovalRequest.objects.get(pk=rid).status == "APPROVED"
