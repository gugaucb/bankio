"""Auth journeys in real browser: login, invalid, lockout, OTP/MFA, logout."""
from conftest import ADMIN, CUSTOMER, CUSTOMER_PW, BASE_URL, STAFF_PW, login, otp_code


def test_login_success_reaches_dashboard(page):
    login(page)
    assert "/login/" not in page.url
    assert page.locator("text=Dashboard").count() > 0 or page.locator("#page").count() > 0


def test_login_invalid_shows_error(page):
    page.goto(f"{BASE_URL}/login/")
    page.fill("input[name=username]", CUSTOMER)
    page.fill("input[name=password]", "wrong-password")
    page.click("button[type=submit]")
    page.wait_for_load_state()
    assert "/login/" in page.url
    assert page.locator(".bg-red-50").count() > 0


def test_login_unknown_user_stays_on_form(page):
    page.goto(f"{BASE_URL}/login/")
    page.fill("input[name=username]", "ghost.user.42")
    page.fill("input[name=password]", "whatever!1")
    page.click("button[type=submit]")
    assert "/login/" in page.url


def test_lockout_after_five_failed_logins(page):
    for _ in range(5):
        page.goto(f"{BASE_URL}/login/")
        page.fill("input[name=username]", CUSTOMER)
        page.fill("input[name=password]", "bad-pass")
        page.click("button[type=submit]")
        page.wait_for_load_state()
    page.goto(f"{BASE_URL}/login/")
    page.fill("input[name=username]", CUSTOMER)
    page.fill("input[name=password]", CUSTOMER_PW)
    page.click("button[type=submit]")
    body = page.content().lower()
    assert "locked" in body or "/login/" in page.url
    # cleanup: unlock so later journeys can sign in
    from conftest import db
    db("from django.contrib.auth import get_user_model\n"
       f"u=get_user_model().objects.get(username='{CUSTOMER}')\n"
       "u.locked_until=None; u.failed_login_count=0; u.save()")


def test_mfa_login_via_otp(page):
    from conftest import db
    # flag MFA on for the admin user directly, then exercise the browser OTP flow
    db("from django.contrib.auth import get_user_model\n"
       "u=get_user_model().objects.get(username='admin')\n"
       "u.mfa_enabled=True; u.save()\nprint('ok')")
    login(page, ADMIN, STAFF_PW)
    assert "/otp/" in page.url, "expected redirect to OTP screen"
    code = otp_code(ADMIN)
    page.fill("input[name=code]", code)
    page.click("button[type=submit]")
    page.wait_for_load_state()
    assert "/otp/" not in page.url
    # cleanup
    db("from django.contrib.auth import get_user_model\n"
       "u=get_user_model().objects.get(username='admin')\n"
       "u.mfa_enabled=False; u.mfa_secret=''; u.save()")


def test_logout_ends_session(page):
    login(page)
    page.goto(f"{BASE_URL}/logout/")
    page.wait_for_load_state()
    # app pages now require login again
    page.goto(f"{BASE_URL}/app/")
    assert "/login/" in page.url
