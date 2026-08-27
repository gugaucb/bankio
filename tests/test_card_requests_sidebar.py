"""Regression: /manage/card-requests/ must use the shared manager layout so the
sidebar renders (it previously overrode {% block content %} and lost the nav)."""
import pytest
from django.test import Client

from tests.conftest import make_user


@pytest.mark.django_db
def test_card_requests_page_shows_sidebar(aubrey, user_factory):
    mgr = make_user("sb_mgr", role="MANAGER")
    from apps.managerops.models import ManagerProfile

    ManagerProfile.objects.create(user=mgr, level="RELATIONSHIP_MANAGER")
    c = Client(enforce_csrf_checks=False)
    c.force_login(mgr)
    r = c.get("/manage/card-requests/")
    assert r.status_code == 200
    body = r.content.decode()
    assert "/manage/" in body and "Bankio Ops" in body          # sidebar brand
    for nav in ("/manage/customers/", "/manage/card-requests/",
                "/manage/approvals/", "/manage/restrictions/"):
        assert f'href="{nav}"' in body, nav
    assert "Card Requests" in body
