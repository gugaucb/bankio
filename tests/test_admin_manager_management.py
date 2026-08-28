"""Admin manager management: create (with ManagerProfile), block/unblock,
session invalidation, RBAC guards — reusing the existing admin users panel."""
import pytest
from django.test import Client

from apps.identity.admin_services import AdminUserError, block_user, create_user, unblock_user
from apps.identity.models import Role, User
from apps.managerops.models import BankBranch, ManagerProfile
from tests.conftest import make_user


@pytest.fixture
def admin(db):
    a = make_user("root_admin", role=Role.ADMIN, password="Admin-Pass-1")
    a.is_superuser = True
    a.save()
    return a


@pytest.mark.django_db
def test_create_manager_gets_profile(admin):
    branch = BankBranch.objects.create(branch_code="7001", name="Admin North", region="NORTH")
    u = create_user(actor=admin, username="new_mgr", email="newmgr@x.io",
                    password="Manager-Pw-9", role=Role.MANAGER, branch_id=branch.pk)
    prof = ManagerProfile.objects.get(user=u)
    assert prof.branch_id == branch.pk
    # manager can reach managerops (get_manager_profile no longer denied)
    from apps.managerops.access import get_manager_profile

    assert get_manager_profile(u).pk == prof.pk


@pytest.mark.django_db
def test_create_manager_without_branch(admin):
    u = create_user(actor=admin, username="nobranch_mgr", email="nb@x.io",
                    password="Manager-Pw-9", role=Role.MANAGER)
    assert ManagerProfile.objects.filter(user=u).exists()


@pytest.mark.django_db
def test_create_invalid_branch(admin):
    with pytest.raises(AdminUserError) as e:
        create_user(actor=admin, username="bad_mgr", email="b@x.io",
                    password="Manager-Pw-9", role=Role.MANAGER, branch_id=99999)
    assert e.value.code == "INVALID_BRANCH"
    assert not User.objects.filter(username="bad_mgr").exists()  # atomic rollback


@pytest.mark.django_db
def test_non_admin_cannot_create_manager(aubrey):
    with pytest.raises(Exception):
        create_user(actor=aubrey, username="evil_mgr", email="e@x.io",
                    password="Manager-Pw-9", role=Role.MANAGER)
    assert not User.objects.filter(username="evil_mgr").exists()


@pytest.mark.django_db
def test_block_manager_kills_session_and_blocks_login(admin):
    u = create_user(actor=admin, username="blk_mgr", email="blk@x.io",
                    password="Manager-Pw-9", role=Role.MANAGER)
    c = Client(enforce_csrf_checks=False)
    c.force_login(u)
    assert c.get("/manage/").status_code in (302, 200)  # session works while active
    block_user(actor=admin, user_id=u.pk, reason=" misconduct investigation ")
    u.refresh_from_db()
    assert not u.is_active
    assert not c.get("/manage/", follow=False).status_code == 200  # session dead -> redirect/403
    # new login refused
    c2 = Client(enforce_csrf_checks=False)
    r = c2.post("/login/", {"username": "blk_mgr", "password": "Manager-Pw-9"})
    assert not c2.session.get("_auth_user_id")
    # unblock restores
    unblock_user(actor=admin, user_id=u.pk, reason="cleared")
    u.refresh_from_db()
    assert u.is_active
    c3 = Client(enforce_csrf_checks=False)
    c3.post("/login/", {"username": "blk_mgr", "password": "Manager-Pw-9"})
    assert c3.session.get("_auth_user_id")


@pytest.mark.django_db
def test_customer_cannot_reach_admin_users_panel(aubrey):
    c = Client(enforce_csrf_checks=False)
    c.force_login(aubrey)
    assert c.get("/manage/users/?role=MANAGER").status_code == 403
    assert c.post("/manage/users/new/", {"username": "x", "email": "x@x.io",
                                         "password": "whatever-1", "role": "MANAGER"}).status_code == 403


@pytest.mark.django_db
def test_manager_role_filter_lists_managers_only(admin):
    create_user(actor=admin, username="flt_mgr", email="flt@x.io",
                password="Manager-Pw-9", role=Role.MANAGER)
    c = Client(enforce_csrf_checks=False)
    c.force_login(admin)
    r = c.get("/manage/users/", {"role": "MANAGER"})
    body = r.content.decode()
    assert "flt_mgr" in body
    assert "root_admin" not in body
