"""ADMIN institutional login on /manager/login/, /manage/ redirect, and
role-contextual sidebar (admin sees Users/Managers, not managerops links)."""
import pytest
from django.test import Client

from apps.identity.models import User
from tests.conftest import make_user

PW = "Admin!12345"


@pytest.fixture
def admin_user(db):
    return make_user("portal_admin", role="ADMIN", password=PW, is_superuser=True)


@pytest.mark.django_db
def test_admin_login_via_manager_portal(admin_user):
    c = Client(enforce_csrf_checks=False)
    r = c.post("/manager/login/", {"username": "portal_admin", "password": PW})
    assert r.status_code == 302 and "/manage/users" in r.url
    assert c.session.get("_auth_user_id") == str(admin_user.pk)


@pytest.mark.django_db
def test_customer_still_denied_on_manager_portal(aubrey):
    c = Client(enforce_csrf_checks=False)
    r = c.post("/manager/login/", {"username": "aubrey", "password": "Test!12345"})
    assert r.status_code == 403
    assert not c.session.get("_auth_user_id")


@pytest.mark.django_db
def test_admin_manage_dashboard_redirects_to_users(admin_user):
    c = Client(enforce_csrf_checks=False)
    c.post("/manager/login/", {"username": "portal_admin", "password": PW})
    r = c.get("/manage/")
    assert r.status_code == 302 and "/manage/users" in r.url


@pytest.mark.django_db
def test_manager_login_still_lands_on_dashboard(db):
    from apps.managerops.models import BankBranch, ManagerProfile

    branch = BankBranch.objects.create(branch_code="2001", name="Uptown", region="SOUTH")
    mgr = make_user("portal_mgr", role="MANAGER", password="Mgr!12345")
    ManagerProfile.objects.create(user=mgr, level="BRANCH_MANAGER", branch=branch)
    c = Client(enforce_csrf_checks=False)
    r = c.post("/manager/login/", {"username": "portal_mgr", "password": "Mgr!12345"})
    assert r.status_code == 302 and r.url.endswith("/manage/")


@pytest.mark.django_db
def test_sidebar_contextual_by_role(admin_user):
    c = Client(enforce_csrf_checks=False)
    c.post("/manager/login/", {"username": "portal_admin", "password": PW})
    html = c.get("/manage/users/").content.decode()
    assert "Users" in html and "Managers" in html
    assert "/manage/customers/" not in html          # managerops links hidden
    assert "/manage/funding/" not in html


@pytest.mark.django_db
def test_manager_sidebar_hides_admin_links(db):
    from apps.managerops.models import BankBranch, ManagerProfile

    branch = BankBranch.objects.create(branch_code="2002", name="Midtown", region="EAST")
    mgr = make_user("portal_mgr2", role="MANAGER", password="Mgr!12345")
    ManagerProfile.objects.create(user=mgr, level="BRANCH_MANAGER", branch=branch)
    c = Client(enforce_csrf_checks=False)
    c.force_login(mgr)
    html = c.get("/manage/").content.decode()
    assert "/manage/customers/" in html
    assert "Managers" not in html                     # admin links hidden
