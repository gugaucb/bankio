"""Browser E2E: TOTP MFA enrollment via QR + login with authenticator codes."""
import re
import time

import pyotp

from conftest import BASE_URL, CUSTOMER, CUSTOMER_PW, db, sh, login


def _skip_tour(page):
    """First-access tour overlay intercepts clicks; skip via Escape."""
    try:
        if page.locator(".driver-popover").count():
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
    except Exception:
        pass


def _provision_user():
    uname = f"totp.e2e.{int(time.time())}"
    r = sh("docker", "compose", "exec", "-T", "web", "python", "manage.py", "shell", "-c",
           f"from django.contrib.auth import get_user_model\n"
           f"get_user_model().objects.create_user(username='{uname}', email='{uname}@t.io', "
           f"password='Totp-E2E-9x', role='CUSTOMER')\nprint('ok')")
    assert r.returncode == 0, r.stderr[-500:]
    return uname


def _totp_for(uname):
    """Decrypt the enrolled secret inside the container to compute codes."""
    out = db(f"from django.contrib.auth import get_user_model\n"
             f"from apps.identity.services import _fernet\n"
             f"u=get_user_model().objects.get(username='{uname}')\n"
             f"print(_fernet().decrypt(u.totp_secret_enc.encode()).decode() if u.totp_secret_enc else 'NONE')").splitlines()[-1]
    assert out != "NONE", "no totp secret enrolled"
    return pyotp.TOTP(out)


def test_totp_enrollment_and_login_flow(page):
    uname = _provision_user()
    # 1. enable via security page
    login(page, uname, "Totp-E2E-9x")
    _skip_tour(page)
    page.goto(f"{BASE_URL}/app/security/")
    page.click("button[name=totp_setup]")
    page.wait_for_load_state()
    assert page.locator("svg").count() >= 1                      # QR rendered locally
    manual = page.locator("code").first.inner_text().strip()     # manual key shown
    assert re.fullmatch(r"[A-Z2-7]{16,}", manual)
    # 2. wrong code keeps MFA disabled
    page.fill("input[name=mfa_code]", "000000")
    page.click("button[name=totp_confirm]")
    page.wait_for_load_state()
    assert "Invalid or expired code" in page.content()
    assert "Disabled" in page.content()
    # 3. correct code enables
    t = _totp_for(uname)
    page.fill("input[name=mfa_code]", t.now())
    page.click("button[name=totp_confirm]")
    page.wait_for_load_state()
    assert "MFA enabled" in page.content()
    # 4. logout
    page.click("button:has-text('Sign out')")
    page.wait_for_load_state()
    # 5. login with password -> MFA required
    page.goto(f"{BASE_URL}/login/")
    page.fill("input[name=username]", uname)
    page.fill("input[name=password]", "Totp-E2E-9x")
    page.click("button[type=submit]")
    page.wait_for_load_state()
    assert "/otp/" in page.url
    # 6. wrong TOTP denied
    page.fill("input[name=code]", "000000")
    page.click("button[type=submit]")
    page.wait_for_load_state()
    assert "Invalid code" in page.content()
    # 7. correct TOTP completes login
    code = t.now()
    page.fill("input[name=code]", code)
    page.click("button[type=submit]")
    page.wait_for_load_state()
    _skip_tour(page)
    assert "/otp/" not in page.url
    assert page.locator("aside, nav").count() >= 1
    # 8. replay: same code reused cannot re-authenticate another login
    page.click("button:has-text('Sign out')")
    page.wait_for_load_state()
    page.goto(f"{BASE_URL}/login/")
    page.fill("input[name=username]", uname)
    page.fill("input[name=password]", "Totp-E2E-9x")
    page.click("button[type=submit]")
    page.wait_for_load_state()
    page.fill("input[name=code]", code)
    page.click("button[type=submit]")
    page.wait_for_load_state()
    assert "Invalid code" in page.content()                      # replay refused


def test_totp_secret_never_in_audit_logs(page):
    uname = _provision_user()
    login(page, uname, "Totp-E2E-9x")
    _skip_tour(page)
    page.goto(f"{BASE_URL}/app/security/")
    page.click("button[name=totp_setup]")
    page.wait_for_load_state()
    t = _totp_for(uname)
    page.fill("input[name=mfa_code]", t.now())
    page.click("button[name=totp_confirm]")
    page.wait_for_load_state()
    secret = t.secret
    out = db(f"from apps.audit.models import AuditLog\n"
             f"from django.contrib.auth import get_user_model\n"
             f"u=get_user_model().objects.get(username='{uname}')\n"
             f"print(any('{secret}' in str(m) for m in u.audit_events.values_list('metadata', flat=True)))").splitlines()[-1]
    assert out.strip() == "False"
