"""Customer app pages: access matrix, card request flow, password change."""
import pytest
from apps.cards.models import Card, CardRequest

pytestmark = pytest.mark.django_db


def login(client, username, password="Test!12345"):
    return client.post("/login/", {"username": username, "password": password})


PAGES = ["/app/", "/app/analytics/", "/app/accounts/", "/app/transactions/",
         "/app/investments/", "/app/cards/", "/app/security/", "/app/settings/"]


@pytest.mark.parametrize("page", PAGES)
def test_anonymous_redirected(client, page):
    assert client.get(page).status_code == 302


def test_customer_sees_all_pages(aubrey, account_factory, client):
    account_factory(aubrey)
    assert login(client, "aubrey").status_code == 302
    for page in PAGES:
        resp = client.get(page)
        assert resp.status_code == 200, page


def test_manager_blocked_from_customer_app(user_factory, client):
    user_factory("mgrx", role="MANAGER")
    assert login(client, "mgrx").status_code == 302
    resp = client.get("/app/cards/")
    assert resp.status_code in (302, 403)


def test_dashboard_real_metrics_not_hardcoded(aubrey, account_factory, client):
    account_factory(aubrey, balance="0", number="90000001")
    login(client, "aubrey")
    html = client.get("/app/").content.decode()
    assert "$20,751" not in html and "$5,200.00" not in html
    assert "spendChart" in html


def test_card_request_and_manager_approval(aubrey, user_factory, account_factory, client):
    acct = account_factory(aubrey, balance="500", number="90000002")
    login(client, "aubrey")
    resp = client.post("/app/cards/", {
        "request_card": "1", "account": acct.pk,
        "type": "CREDIT_CARD", "limit": "3500",
    })
    assert resp.status_code == 302
    req = CardRequest.objects.get(customer=aubrey)
    assert req.status == "PENDING" and str(req.requested_limit) == "3500.00"

    # duplicate pending blocked
    resp = client.post("/app/cards/", {
        "request_card": "1", "account": acct.pk,
        "type": "CREDIT_CARD", "limit": "1000",
    })
    assert CardRequest.objects.filter(customer=aubrey).count() == 1

    # manager approves with lower limit
    user_factory("mgrcard", role="MANAGER")
    client.logout()
    assert login(client, "mgrcard").status_code == 302
    resp = client.post(f"/manage/card-requests/{req.pk}/decide/", {
        "decision": "approve", "approved_limit": "3000",
    })
    req.refresh_from_db()
    assert req.status == "APPROVED" and str(req.approved_limit) == "3000.00"
    card = Card.objects.get(account=acct)
    assert card.holder_name == aubrey.get_full_name().upper()
    assert str(card.credit_limit) == "3000.00"


def test_card_request_rejection(aubrey, user_factory, account_factory, client):
    acct = account_factory(aubrey, number="90000003")
    login(client, "aubrey")
    client.post("/app/cards/", {"request_card": "1", "account": acct.pk,
                                "type": "DEBIT_CARD", "limit": "1000"})
    user_factory("mgrrj", role="MANAGER")
    client.logout()
    login(client, "mgrrj")
    req = CardRequest.objects.get()
    client.post(f"/manage/card-requests/{req.pk}/decide/", {"decision": "reject"})
    req.refresh_from_db()
    assert req.status == "REJECTED"
    assert not Card.objects.filter(account=acct).exists()


def test_non_manager_cannot_decide_card(aubrey, user_factory, account_factory, client):
    acct = account_factory(aubrey, number="90000004")
    login(client, "aubrey")
    client.post("/app/cards/", {"request_card": "1", "account": acct.pk,
                                "type": "CREDIT_CARD", "limit": "2000"})
    req = CardRequest.objects.get()
    # customer tries to self-approve via manager endpoint
    resp = client.post(f"/manage/card-requests/{req.pk}/decide/", {"decision": "approve"})
    req.refresh_from_db()
    assert resp.status_code in (302, 403)
    assert req.status == "PENDING"


def test_change_password_flow(aubrey, client):
    login(client, "aubrey")
    resp = client.post("/app/security/", {
        "change_password": "1",
        "old_password": "WRONG", "new_password1": "New!23456", "new_password2": "New!23456",
    })
    content = resp.content.decode().lower()
    assert "incorrect" in content or "could not" in content

    resp = client.post("/app/security/", {
        "change_password": "1",
        "old_password": "Test!12345", "new_password1": "New!23456", "new_password2": "New!23456",
    }, follow=True)
    assert resp.status_code == 200
    aubrey.refresh_from_db()
    assert aubrey.check_password("New!23456")
    # session stays authenticated
    assert client.get("/app/security/").status_code == 200
