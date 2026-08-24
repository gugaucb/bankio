"""Branch 2 — admin user-management views: authz, HTTP semantics, flows."""
import pytest
from django.contrib.auth import authenticate

from apps.identity.models import User
from tests.conftest import make_user


@pytest.fixture
def admin(db):
    return make_user("view-admin", role="ADMIN", password="Admin!12345")


@pytest.fixture
def admin_client(client, admin):
    client.force_login(admin)
    return client


@pytest.fixture
def target(db):
    return make_user("viewtarget", password="Target!12345")


# ------------------------------------------------------------------- authz

@pytest.mark.django_db
def test_anonymous_redirected_to_login(db, client):
    r = client.get("/manage/users/")
    assert r.status_code == 302 and "/login" in r["Location"]


@pytest.mark.django_db
def test_common_user_denied(db):
    from django.test import Client

    u = make_user("pleb")
    c = Client()
    c.force_login(u)
    for url in ("/manage/users/", "/manage/users/new/", "/manage/users/1/"):
        assert c.get(url).status_code == 403
    # staff (non-admin role) also denied
    s = make_user("supportguy", role="SUPPORT_AGENT")
    c.force_login(s)
    assert c.get("/manage/users/").status_code == 403
    assert u.role != "ADMIN"


@pytest.mark.django_db
def test_get_on_block_is_405(admin_client, target):
    assert admin_client.get(f"/manage/users/{target.pk}/block/").status_code == 405
    assert admin_client.get(f"/manage/users/{target.pk}/unblock/").status_code == 405


@pytest.mark.django_db
def test_superuser_allowed(db):
    from django.test import Client

    root = User.objects.create_user("suroot", email="su@t.io",
                                    password="Xx!12345678", is_superuser=True)
    c = Client()
    c.force_login(root)
    assert c.get("/manage/users/").status_code == 200


# -------------------------------------------------------------------- list

@pytest.mark.django_db
def test_list_renders_and_filters(admin_client):
    make_user("filterme")
    r = admin_client.get("/manage/users/")
    assert r.status_code == 200 and b"filterme" in r.content
    r = admin_client.get("/manage/users/?q=filterme&status=ACTIVE&role=CUSTOMER")
    assert r.status_code == 200 and b"filterme" in r.content
    r = admin_client.get("/manage/users/?q=nomatch-xyz")
    assert b"No users match" in r.content


@pytest.mark.django_db
def test_pagination_renders(admin_client):
    for i in range(25):
        make_user(f"pguser{i}")
    r = admin_client.get("/manage/users/")
    assert r.status_code == 200
    assert b"pguser" in r.content  # page 1 shows first slice


# ------------------------------------------------------------------ create

@pytest.mark.django_db
def test_create_flow_via_view(admin_client):
    r = admin_client.post("/manage/users/new/", {
        "username": "created-via-ui", "email": "cui@t.io",
        "password": "Sup3r-Secret!pass", "role": "CUSTOMER",
        "first_name": "C", "last_name": "V", "phone": "",
    })
    assert r.status_code == 302
    assert authenticate(username="created-via-ui", password="Sup3r-Secret!pass")
    r = admin_client.post("/manage/users/new/", {
        "username": "weak-one", "email": "w@t.io", "password": "123",
        "role": "CUSTOMER",
    })
    assert not User.objects.filter(username="weak-one").exists()


@pytest.mark.django_db
def test_create_duplicate_shows_error(admin_client):
    make_user("taken-name")
    r = admin_client.post("/manage/users/new/", {
        "username": "fresh-x", "email": "taken-name@t.io",
        "password": "Sup3r-Secret!pass", "role": "CUSTOMER",
    })
    assert r.status_code == 200 and not User.objects.filter(username="fresh-x").exists()


# ------------------------------------------------------------ detail/block

@pytest.mark.django_db
def test_detail_renders_and_404(admin_client, target):
    r = admin_client.get(f"/manage/users/{target.pk}/")
    assert r.status_code == 200 and b"viewtarget" in r.content
    assert admin_client.get("/manage/users/99999/").status_code == 404


@pytest.mark.django_db
def test_block_unblock_flow_via_views(admin_client, target):
    r = admin_client.post(f"/manage/users/{target.pk}/block/", {"reason": "suspeita"})
    assert r.status_code == 302
    target.refresh_from_db()
    assert target.is_active is False
    assert authenticate(username="viewtarget", password="Target!12345") is None

    r = admin_client.post(f"/manage/users/{target.pk}/unblock/", {"reason": "ok"})
    assert r.status_code == 302
    target.refresh_from_db()
    assert target.is_active is True
    assert authenticate(username="viewtarget", password="Target!12345") == target


@pytest.mark.django_db
def test_block_without_reason_fails_cleanly(admin_client, target):
    r = admin_client.post(f"/manage/users/{target.pk}/block/", {"reason": ""})
    assert r.status_code == 302
    target.refresh_from_db()
    assert target.is_active is True  # unchanged


@pytest.mark.django_db
def test_self_block_via_view_rejected(admin_client, admin):
    r = admin_client.post(f"/manage/users/{admin.pk}/block/", {"reason": "self"})
    assert r.status_code == 302
    admin.refresh_from_db()
    assert admin.is_active is True
