"""Browser E2E: restrictions screen — empty state, listing, compliance guard."""
from conftest import BASE_URL, MANAGER, STAFF_PW, db, login


def test_restrictions_listing_with_data(page):
    # self-seed one restriction on a customer assigned to manager1
    out = db("from apps.accounts.models import Account\n"
             "from apps.managerops.models import AccountRestriction, CustomerManagerAssignment\n"
             "from django.contrib.auth import get_user_model\n"
             "m=get_user_model().objects.get(username='manager1')\n"
             "cust_ids=list(CustomerManagerAssignment.objects.filter(manager=m, status='ACTIVE')"
             ".values_list('customer_id', flat=True))\n"
             "a=Account.objects.filter(customer_id__in=cust_ids, status='ACTIVE').first()\n"
             "r=AccountRestriction.objects.create(account=a, restriction_type='TRANSFER_BLOCK', "
             "reason='browser e2e listing restriction', requested_by=m)\n"
             "print(r.pk)").splitlines()[-1]
    login(page, MANAGER, STAFF_PW)
    page.goto(f"{BASE_URL}/manage/restrictions/")
    body = page.content()
    assert "Active Restrictions" in body
    assert "browser e2e listing restriction" in body
    assert "TRANSFER_BLOCK" in body


def test_restrictions_lift_and_compliance_guard(page):
    login(page, MANAGER, STAFF_PW)
    page.goto(f"{BASE_URL}/manage/restrictions/")
    # AML row (if present) has no Lift button
    aml_rows = page.locator("tr", has_text="AML_HOLD")
    for i in range(aml_rows.count()):
        assert aml_rows.nth(i).locator("form[action*='/lift/']").count() == 0
    # lift the e2e restriction
    btn = page.locator("form[action*='/lift/']").first
    if btn.count():
        btn.locator("button").click()
        page.wait_for_load_state()
        assert "Server Error" not in page.content()
        out = db("from apps.managerops.models import AccountRestriction\n"
                 "print(AccountRestriction.objects.filter(reason='browser e2e listing restriction', "
                 "active=True).count())").splitlines()[-1]
        assert out.strip() == "0"


def test_restrictions_empty_state_for_new_manager(page):
    import time
    from conftest import sh
    uname = f"empty.mgr.{int(time.time())}"
    r = sh("docker", "compose", "exec", "-T", "web", "python", "manage.py", "shell", "-c",
           f"from django.contrib.auth import get_user_model\n"
           f"from apps.managerops.models import ManagerProfile\n"
           f"u=get_user_model().objects.create_user(username='{uname}', email='{uname}@t.io', "
           f"password='Empty-Mgr-1', role='MANAGER')\n"
           f"ManagerProfile.objects.create(user=u, level='RELATIONSHIP_MANAGER')\nprint('ok')")
    assert r.returncode == 0, r.stderr[-500:]
    login(page, uname, "Empty-Mgr-1")
    page.goto(f"{BASE_URL}/manage/restrictions/")
    assert "No active restrictions" in page.content()
