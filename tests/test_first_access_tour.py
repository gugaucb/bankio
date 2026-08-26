"""FASE 9 B1 — first-access tour core: server-side state, RBAC-safe flow."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.identity.models import TourProgress
from apps.identity import tour as tour_mod

from .conftest import make_user


@pytest.fixture
def cust_user(db):
    u = make_user("touruser")
    from apps.customers.models import Customer
    Customer.objects.create(user=u, customer_number="C-TOUR")
    return u


def _login(client, username="touruser", pw="Test!12345"):
    assert client.post("/login/", {"username": username, "password": pw}).status_code == 302


def _account_for(u):
    from apps.accounts.models import Account
    from apps.ledger.services import get_or_create_account
    la = get_or_create_account(f"2001-TOUR-{u.pk}", f"A {u.username}", is_customer=True)
    return Account.objects.create(customer=u, account_number=f"77{u.pk:010d}", ledger_account=la)


def test_no_row_means_show_tour(cust_user, client):
    _login(client)
    r = client.get("/app/")
    assert r.context["show_tour"] is True
    assert b"tour-steps" in r.content


def test_completed_tour_does_not_autostart(cust_user, client):
    _login(client)
    tour_mod.mark_completed(cust_user)
    assert client.get("/app/").context["show_tour"] is False


def test_skipped_tour_does_not_autostart(cust_user, client):
    _login(client)
    tour_mod.mark_skipped(cust_user)
    assert client.get("/app/").context["show_tour"] is False


def test_complete_endpoint_persists_and_stops_auto_start(cust_user, client):
    _login(client)
    r = client.post("/app/tour/complete/")
    assert r.status_code == 302
    p = TourProgress.objects.get(user=cust_user)
    assert p.completed_at is not None and p.skipped_at is None
    assert client.get("/app/").context["show_tour"] is False


def test_skip_endpoint_persists(cust_user, client):
    _login(client)
    assert client.post("/app/tour/skip/").status_code == 302
    assert TourProgress.objects.get(user=cust_user).skipped_at is not None


def test_finish_endpoints_reject_get(cust_user, client):
    _login(client)
    assert client.get("/app/tour/complete/").status_code == 405
    assert client.get("/app/tour/skip/").status_code == 405
    assert not TourProgress.objects.filter(user=cust_user).exists()


def test_unknown_outcome_404(cust_user, client):
    _login(client)
    assert client.post("/app/tour/whatever/").status_code == 404


def test_replay_flow_is_server_side_one_shot(cust_user, client):
    _login(client)
    tour_mod.mark_completed(cust_user)          # already done once
    assert client.get("/app/").context["show_tour"] is False
    r = client.get("/app/tour/replay/")          # Ajuda → Ver tutorial novamente
    assert r.status_code == 302
    assert client.get("/app/").context["show_tour"] is True   # shown again
    assert client.get("/app/").context["show_tour"] is False  # one-shot only


def test_customer_steps_never_reference_staff_screens():
    steps = tour_mod.customer_steps()
    blob = str(steps).lower()
    for forbidden in ("fraud", "secops", "manage/", "admin", "approvals", "restrictions"):
        assert forbidden not in blob, forbidden
    # every targeted hook must be a nav-* (customer shell) or welcome popover
    for s in steps:
        if "element" in s:
            assert s["element"].startswith('[data-tour="nav-')


def test_staff_steps_only_reference_authorized_resources():
    blob = str(tour_mod.staff_steps()).lower()
    for forbidden in ("fraud", "secops", "manage/users", "approvals"):
        assert forbidden not in blob, forbidden


def test_tour_state_requires_authentication(client, db):
    from django.contrib.auth.models import AnonymousUser
    class FakeSession(dict):
        def get(self, k, d=None): return dict.get(self, k, d)
    show, _ = tour_mod.tour_state(AnonymousUser(), FakeSession())
    assert show is False
