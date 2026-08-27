"""Customer 360 full business details: every existing business field is visible to
an authorized manager; authentication secrets are never rendered; cross-branch
managers cannot open another branch's customer (IDOR-safe)."""
import pytest
from django.test import Client

from apps.customers.models import Customer
from apps.managerops.models import CustomerManagerAssignment
from tests.conftest import make_user
from tests.test_manager_portal import make_manager


@pytest.fixture
def scene(db):
    b1 = make_manager("fd_mgr", "BRANCH_MANAGER", __import__(
        "apps.managerops.models", fromlist=["BankBranch"]).BankBranch.objects.create(
        branch_code="1100", name="FD North", region="NORTH"))
    b2 = make_manager("fd_other", "BRANCH_MANAGER", __import__(
        "apps.managerops.models", fromlist=["BankBranch"]).BankBranch.objects.create(
        branch_code="2200", name="FD South", region="SOUTH"))
    cust_user = make_user("fd_cust", password="Test!12345", last_name="Detail",
                          phone="+15550111")
    cust_user.first_name = "Frida"
    cust_user.email = "frida@detail.io"
    cust_user.mfa_secret = "deadbeefcafe"
    cust_user.set_password("Secret-Pass-9")
    cust_user.save()
    from apps.customers.models import Customer

    cust = Customer.objects.create(user=cust_user, customer_number="CUST-FD1", branch=b1.manager_profile.branch,
                                   address="7 Ledger Lane", occupation="Mason",
                                   monthly_income="4200", status="ACTIVE")
    CustomerManagerAssignment.objects.create(customer=cust_user, manager=b1,
                                             branch=b1.manager_profile.branch)
    return {"mgr": b1, "other": b2, "cust_user": cust_user, "cust": cust}


@pytest.mark.django_db
def test_full_details_visible_to_authorized_manager(scene):
    c = Client(enforce_csrf_checks=False)
    c.force_login(scene["mgr"])
    r = c.get(f"/manage/customers/{scene['cust_user'].pk}/")
    assert r.status_code == 200
    body = r.content.decode()
    for expected in ("frida@detail.io", "+15550111", "7 Ledger Lane", "Mason",
                     "CUST-FD1", "4,200", "USD", "RETAIL"):
        assert expected in body, expected


@pytest.mark.django_db
def test_no_auth_secrets_in_html(scene):
    c = Client(enforce_csrf_checks=False)
    c.force_login(scene["mgr"])
    r = c.get(f"/manage/customers/{scene['cust_user'].pk}/")
    body = r.content.decode()
    assert "deadbeefcafe" not in body          # mfa/otp secret never rendered
    assert "pbkdf2" not in body                # password hash never rendered
    assert "Secret-Pass-9" not in body         # password never rendered


@pytest.mark.django_db
def test_out_of_authority_manager_cannot_open_details(scene):
    c = Client(enforce_csrf_checks=False)
    c.force_login(scene["other"])
    r = c.get(f"/manage/customers/{scene['cust_user'].pk}/")
    assert r.status_code in (403, 404)