"""Browser E2E: ADMIN on the institutional login lands on Users/Managers;
managerops links are hidden contextually per role."""
import re

from conftest import ADMIN, BASE_URL, MANAGER, STAFF_PW


def _inst_login(page, username, password):
    page.goto(f"{BASE_URL}/manager/login/")
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", password)
    page.click("form[action='/manager/login/'] button")


def test_admin_lands_on_users_panel(page):
    _inst_login(page, ADMIN, STAFF_PW)
    page.wait_for_url(re.compile(r"/manage/users"))
    sidebar = page.locator("aside").inner_text()
    assert "Users" in sidebar and "Managers" in sidebar
    assert "/manage/customers/" not in page.content()
    # /manage/ routes admins to their panel instead of 403
    page.goto(f"{BASE_URL}/manage/")
    page.wait_for_url(re.compile(r"/manage/users"))


def test_manager_sidebar_hides_admin_links(page):
    _inst_login(page, MANAGER, STAFF_PW)
    page.wait_for_url(re.compile(r"/manage/$"))
    sidebar = page.locator("aside").inner_text()
    assert "Customers" in sidebar
    assert "Managers" not in sidebar
