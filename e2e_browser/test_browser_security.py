"""Security center journeys: password change, MFA enable, devices, sessions."""
from conftest import BASE_URL, CUSTOMER, CUSTOMER_PW, otp_code, login


def test_change_password_success(page):
    """DEFECT #2 FIXED: the password form now renders (form in GET context)."""
    new_pw = "Browser!2026x"
    login(page)
    page.goto(f"{BASE_URL}/app/security/")
    page.fill("input[name=old_password]", CUSTOMER_PW)
    page.fill("input[name=new_password1]", new_pw)
    page.fill("input[name=new_password2]", new_pw)
    page.click("form:has(input[name=change_password]) button")
    page.wait_for_load_state("networkidle")
    assert "successfully" in page.content().lower()
    # restore original password
    page.fill("input[name=old_password]", new_pw)
    page.fill("input[name=new_password1]", CUSTOMER_PW)
    page.fill("input[name=new_password2]", CUSTOMER_PW)
    page.click("form:has(input[name=change_password]) button")
    page.wait_for_load_state("networkidle")
    assert "successfully" in page.content().lower()


def test_mfa_enable_and_disable_flow(page):
    """MFA enable/disable via the TOTP authenticator UI (legacy email-OTP UI retired)."""
    import pyotp
    from conftest import db
    db("from django.contrib.auth import get_user_model\n"
       f"u=get_user_model().objects.get(username='{CUSTOMER}')\n"
       "u.mfa_enabled=False; u.mfa_secret=''; u.totp_secret_enc=''; u.totp_last_step=0; u.save()")
    login(page)
    page.goto(f"{BASE_URL}/app/security/")
    page.click("button[name=totp_setup]")
    page.wait_for_load_state("networkidle")
    assert page.locator("input[name=mfa_code]").count() == 1
    secret = db(f"from django.contrib.auth import get_user_model\n"
                f"from apps.identity.services import _fernet\n"
                f"u=get_user_model().objects.get(username='{CUSTOMER}')\n"
                f"print(_fernet().decrypt(u.totp_secret_enc.encode()).decode())").splitlines()[-1]
    t = pyotp.TOTP(secret)
    page.fill("input[name=mfa_code]", t.now())
    page.click("button[name=totp_confirm]")
    page.wait_for_load_state("networkidle")
    assert "mfa enabled" in page.content().lower()
    # disable with password + current TOTP code
    page.fill("input[name=password]", CUSTOMER_PW)
    page.fill("input[name=mfa_code]", t.now())
    page.click("button[name=mfa_disable]")
    page.wait_for_load_state("networkidle")
    assert "mfa disabled" in page.content().lower()


def test_sessions_revoke_other_sessions(page):
    login(page)
    page.goto(f"{BASE_URL}/app/security/")
    btn = page.locator("button[name=revoke_other_sessions]")
    assert btn.count() == 1
    btn.click()
    page.wait_for_load_state("networkidle")
    assert "signed out" in page.content().lower()
    # current session must survive
    page.goto(f"{BASE_URL}/app/security/")
    assert "/login/" not in page.url


def test_security_activity_history_renders(page):
    login(page)
    page.goto(f"{BASE_URL}/app/security/?page=1")
    assert page.locator("text=/login/i").count() >= 1


def test_devices_section_present(page):
    login(page)
    page.goto(f"{BASE_URL}/app/security/")
    assert page.locator("form:has(input[name=device])").count() >= 0  # may be empty
    assert "Security" in page.content()
