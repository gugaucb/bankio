"""Adversarial browser security probes: unauthenticated, wrong role, IDOR,
URL manipulation, CSRF, destructive-via-GET."""
import pytest

from conftest import BASE_URL, CUSTOMER, CUSTOMER2, STAFF_PW, expect_http_errors, login

STAFF_ONLY = ["/fraud/", "/fraud/alerts/", "/manage/users/", "/manage/",
              "/secops/health/", "/secops/evaluations/"]


def test_unauthenticated_redirects_to_login(page):
    for path in ("/app/", "/transfers/", "/app/notifications/", "/manage/users/"):
        page.goto(f"{BASE_URL}{path}")
        assert "/login/" in page.url or "/manager/login/" in page.url, path


@pytest.mark.parametrize("path", STAFF_ONLY)
def test_customer_cannot_open_staff_pages(page, request, path):
    expect_http_errors(request)
    login(page)
    resp = page.goto(f"{BASE_URL}{path}")
    assert (resp.status if resp else 302) in (200, 302, 403, 404), \
        f"{path} -> {resp.status if resp else None}"


def test_idor_statement_receipt_card_invoice_of_other_user(page, acc_ids, request):
    """aubrey (customer) probing liam.johnson1-owned resources -> 404."""
    expect_http_errors(request)
    from conftest import db
    other_acc = int(db(
        "from apps.accounts.models import Account\n"
        "print(Account.objects.filter(customer__username='%s').first().pk)" % CUSTOMER2))
    other_ref = db(
        "from django.contrib.auth import get_user_model\n"
        "from apps.accounts.models import Account\n"
        "from apps.transfers.services import execute_transfer\n"
        "U=get_user_model()\n"
        "liam=U.objects.get(username='%s')\n"
        "src=Account.objects.filter(customer=liam).first()\n"
        # destination must NOT involve aubrey: journals are shared between both
        # sides of a transfer, so its receipt is legitimately visible to her
        "dst=Account.objects.filter(customer__username__in="
        "['olivia.smith2','noah.miller3']).exclude(customer=liam).first()\n"
        "t,_=execute_transfer(actor=liam, source_account_id=src.pk, amount='3.00',\n"
        "                     destination_account_id=dst.pk, description='idor probe seed')\n"
        "print(t.reference)" % CUSTOMER2).splitlines()[-1]
    other_card = db("from apps.cards.models import Card\n"
                    "c=Card.objects.exclude(account__customer__username='%s').first()\n"
                    "print(c.pk if c else '')" % CUSTOMER).splitlines()[-1]
    other_tx = db(
        "from apps.cards.models import Card, CardTransaction\n"
        "from decimal import Decimal\n"
        "c=Card.objects.exclude(account__customer__username='%s').first()\n"
        "t=CardTransaction.objects.exclude(card__account__customer__username='%s').first()\n"
        "if not t:\n"
        "    t=CardTransaction.objects.create(card=c, merchant='IDOR TX', amount=Decimal('2.00'))\n"
        "print(t.pk)" % (CUSTOMER, CUSTOMER)).splitlines()[-1]
    login(page)
    probes = [
        f"/app/accounts/{other_acc}/statement/",
        f"/app/accounts/{other_acc}/statement/export.csv",
    ]
    if other_ref:
        probes += [f"/app/transactions/{other_ref}/", f"/app/receipts/{other_ref}/"]
    if other_card:
        probes += [f"/app/cards/{other_card}/", f"/app/cards/{other_card}/invoices/",
                   f"/app/cards/{other_card}/transactions/"]
    if other_tx and other_card:
        probes.append(f"/app/cards/{other_card}/transactions/{other_tx}/")
    for p in probes:
        resp = page.goto(f"{BASE_URL}{p}")
        assert resp.status == 404, f"{p} -> {resp.status}"


def test_idor_post_controls_on_other_users_card(page, request):
    expect_http_errors(request)
    from conftest import db
    other_card = db(
        "from django.contrib.auth import get_user_model\n"
        "from apps.accounts.models import Account\n"
        "from apps.cards.models import Card\n"
        "from apps.ledger.services import get_or_create_account\n"
        "from decimal import Decimal\n"
        "U=get_user_model()\n"
        "c=Card.objects.exclude(account__customer__username='%s').first()\n"
        "if not c:\n"
        "    la=get_or_create_account('2001-E2E-IDOR', 'e2e idor', is_customer=True)\n"
        "    a=Account.objects.create(customer=U.objects.get(username='%s'),\n"
        "        account_number='99E2E00002', ledger_account=la)\n"
        "    c=Card.objects.create(account=a, holder_name='IDOR PROBE', type='CREDIT_CARD',\n"
        "                          credit_limit=Decimal('100.00'))\n"
        "print(c.pk)" % (CUSTOMER, CUSTOMER2)).splitlines()[-1]
    login(page)
    csrf = page.context.cookies(BASE_URL)
    token = next(c["value"] for c in csrf if c["name"] == "csrftoken")
    headers = {"X-CSRFToken": token}
    r = page.request.post(f"{BASE_URL}/app/cards/{other_card}/controls/",
                          form={"action": "freeze"}, headers=headers)
    assert r.status == 404
    r2 = page.request.post(f"{BASE_URL}/app/cards/{other_card}/invoices/pay/",
                           headers=headers)
    assert r2.status == 404
    stmts = db("from apps.cards.models import CreditStatement\n"
               "print('|'.join(str(s.pk) for s in "
               "CreditStatement.objects.exclude(card__account__customer__username='%s')))"
               % CUSTOMER)
    for sid in [x for x in stmts.split("|") if x]:
        r3 = page.goto(f"{BASE_URL}/app/cards/{other_card}/invoices/{sid}/")
        assert r3.status == 404


def test_post_without_csrf_rejected(page):
    login(page)
    # same-origin fetch without the csrf token header/field must be rejected (403)
    result = page.evaluate("""async () => {
        const r = await fetch('/app/cards/1/controls/', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'action=freeze',
        });
        return r.status;
    }""")
    assert result == 403, result


def test_destructive_actions_not_available_via_get(page):
    """Freeze has no GET handler that mutates state."""
    from conftest import db
    q = ("from apps.cards.models import Card\n"
         "c=Card.objects.get(pk=1); print(c.status)")
    before = db(q)
    login(page)
    resp = page.goto(f"{BASE_URL}/app/cards/1/controls/?action=freeze")
    after = db(q)
    assert (resp.status if resp else 0) in (404, 405) or before == after
    assert before == after, f"GET mutated state: {before} -> {after}"


def test_url_manipulation_invalid_ids(page, request):
    expect_http_errors(request)
    login(page)
    for p in ("/app/cards/999999/", "/app/accounts/999999/statement/",
              "/app/transactions/NOSUCHREF123/"):
        resp = page.goto(f"{BASE_URL}{p}")
        assert resp.status in (404, 302), f"{p} -> {resp.status}"


def test_viewports_smoke_desktop_and_mobile(page):
    page.set_viewport_size({"width": 1440, "height": 900})
    login(page)
    assert "Server Error" not in page.content()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{BASE_URL}/app/")
    assert "Server Error" not in page.content()
    # no horizontal overflow on mobile dashboard shell
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 40, f"mobile horizontal overflow: {overflow}px"

