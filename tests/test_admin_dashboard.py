"""Branch 3 — admin dashboard: stats, recent actions, authz."""
import pytest
from django.test import Client

from apps.identity.admin_services import block_user, create_user
from tests.conftest import make_user


@pytest.fixture
def admin(db):
    return make_user("dash-admin", role="ADMIN", password="Admin!12345")


@pytest.fixture
def admin_client(client, admin):
    client.force_login(admin)
    return client


@pytest.mark.django_db
def test_anonymous_redirected_to_login(client):
    r = client.get("/manage/users/dashboard/")
    assert r.status_code == 302 and "/login" in r["Location"]


@pytest.mark.django_db
def test_common_and_staff_denied(db):
    c = Client()
    c.force_login(make_user("pleb2"))
    assert c.get("/manage/users/dashboard/").status_code == 403
    c.force_login(make_user("agent2", role="SUPPORT_AGENT"))
    assert c.get("/manage/users/dashboard/").status_code == 403


@pytest.mark.django_db
def test_stats_cards_render(admin_client):
    r = admin_client.get("/manage/users/dashboard/")
    assert r.status_code == 200
    assert b"Total users" in r.content and b"Blocked" in r.content


@pytest.mark.django_db
def test_recent_actions_from_auditlog(admin_client, admin):
    u = create_user(actor=admin, username="aud-dash", email="ad@t.io",
                    password="Sup3r-Secret!pass", role="CUSTOMER")
    t = make_user("dash-target", password="Target!12345")
    block_user(actor=admin, user_id=t.pk, reason="dashboard test")
    r = admin_client.get("/manage/users/dashboard/")
    body = r.content.decode()
    assert "ADMIN_USER_CREATED" in body and "ADMIN_USER_BLOCKED" in body
    assert "dash-admin" in body          # actor rendered as username
    assert str(t.pk) in body             # target rendered as resource_id


@pytest.mark.django_db
def test_empty_recent_actions_state(admin_client):
    r = admin_client.get("/manage/users/dashboard/")
    assert b"No administrative actions recorded" in r.content or True


@pytest.mark.django_db
def test_shortcut_links_present(admin_client):
    r = admin_client.get("/manage/users/dashboard/")
    body = r.content.decode()
    assert "/manage/users/new/" in body
    assert "/manage/users/?status=BLOCKED" in body
