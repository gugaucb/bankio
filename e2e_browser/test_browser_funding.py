"""Browser E2E: manager funding flow (ledger-backed, idempotent)."""
import time

import pytest

from conftest import BASE_URL, MANAGER, STAFF_PW, db, login


def _pick_account():
    # choose an ACTIVE account of a customer visible to manager1 (assignment-based)
    out = db("from apps.accounts.models import Account\n"
             "from apps.managerops.models import CustomerManagerAssignment\n"
             "cust_ids=CustomerManagerAssignment.objects.filter(manager__username='manager1'"
             ", status='ACTIVE').values_list('customer_id', flat=True)\n"
             "a=Account.objects.filter(customer_id__in=cust_ids, status='ACTIVE').first()\n"
             "print('NONE' if a is None else f'{a.pk} {a.account_number} {a.customer.username} {a.current_balance}')\n").splitlines()[-1]
    if out.strip() == "NONE":
        # fall back: create the assignment for the first active account's customer
        out = db("from apps.accounts.models import Account\n"
                 "from django.contrib.auth import get_user_model\n"
                 "from apps.managerops.models import CustomerManagerAssignment\n"
                 "a=Account.objects.filter(status='ACTIVE').select_related('customer').first()\n"
                 "m=get_user_model().objects.get(username='manager1')\n"
                 "CustomerManagerAssignment.objects.get_or_create(customer=a.customer, manager=m)\n"
                 "print(a.pk, a.account_number, a.customer.username, a.current_balance)").splitlines()[-1]
    pk, number, user, bal = out.split()
    return pk, number, user, bal


def test_manager_funding_flow(page):
    login(page, MANAGER, STAFF_PW)
    pk, number, user, before = _pick_account()
    key = f"e2e-fund-{int(time.time())}"

    page.goto(f"{BASE_URL}/manage/funding/")
    assert page.locator("aside").is_visible()          # shared sidebar
    page.select_option("select[name=account]", pk)
    page.fill("input[name=amount]", "77.25")
    page.fill("input[name=idempotency_key]", key)
    page.fill("input[name=reason]", "Browser E2E deposit")
    page.click("button:has-text('Post funding')")
    page.wait_for_load_state()
    body = page.content()
    assert "posted as" in body

    out = db(f"from apps.accounts.models import Account\n"
             f"print(Account.objects.get(pk={pk}).current_balance)").splitlines()[-1]
    assert float(out) == float(before) + 77.25

    # replay: same idempotency key must not create money
    page.select_option("select[name=account]", pk)
    page.fill("input[name=amount]", "77.25")
    page.fill("input[name=idempotency_key]", key)
    page.click("button:has-text('Post funding')")
    page.wait_for_load_state()
    assert "replay ignored" in page.content()
    out2 = db(f"from apps.accounts.models import Account\n"
              f"print(Account.objects.get(pk={pk}).current_balance)").splitlines()[-1]
    assert float(out2) == float(out)

    # negative amount blocked (HTML5 min + server-side validation covered in unit tests)
    page.select_option("select[name=account]", pk)
    page.fill("input[name=amount]", "-5")
    page.fill("input[name=idempotency_key]", key + "-neg")
    page.click("button:has-text('Post funding')")
    page.wait_for_load_state()
    assert page.url.endswith("/manage/funding/") or "manage/funding" in page.url
    out3 = db(f"from apps.accounts.models import Account\n"
              f"print(Account.objects.get(pk={pk}).current_balance)").splitlines()[-1]
    assert float(out3) == float(out)


def test_customer_cannot_reach_funding(page):
    from conftest import CUSTOMER, CUSTOMER_PW, login as cust_login
    cust_login(page, CUSTOMER, CUSTOMER_PW)
    page.goto(f"{BASE_URL}/manage/funding/")
    assert "Profile Details" not in page.content() and page.locator("text=Manager role").count() >= 1 or \
        "/login/" in page.url or "403" in page.url or "Server Error" not in page.content()
    # simplest robust assertion: funding form must NOT be present
    assert page.locator("select[name=account]").count() == 0
