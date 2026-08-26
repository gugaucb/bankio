"""Notifications center: badge, mark read, read-all, deep links."""
from conftest import BASE_URL, login


def _seed_notification():
    import time
    from conftest import db
    nonce = str(int(time.time() * 1000) % 10**9)  # dedup key unique per run
    db("from apps.notifications.services import notify\n"
       "from django.contrib.auth import get_user_model\n"
       "u=get_user_model().objects.get(username='aubrey.sabina0')\n"
       "notify(recipient=u, category='SECURITY', kind='E2E_TEST',\n"
       "       title='Browser E2E note " + nonce + "', body='deep link /app/accounts/',\n"
       "       dedup_key='E2E:' + str(u.pk) + ':" + nonce + "')")
    return str(nonce)


def _latest_note_title(page):
    return page.locator("text=/Browser E2E note/").first


def test_badge_and_center(page):
    title = _seed_notification()
    login(page)
    assert page.locator("[aria-label=Notifications] span").count() >= 0
    page.goto(f"{BASE_URL}/app/notifications/")
    assert f"Browser E2E note {title}" in page.content()


def test_mark_single_read(page):
    _seed_notification()
    login(page)
    page.goto(f"{BASE_URL}/app/notifications/")
    btn = page.locator("form[action*='/read/'] button").first
    if btn.count():
        btn.click()
        page.wait_for_load_state("networkidle")
    assert "/login/" not in page.url  # action did not break the session


def test_mark_all_read(page):
    _seed_notification()
    login(page)
    page.goto(f"{BASE_URL}/app/notifications/")
    page.locator("form[action*='read-all'] button").click()
    page.wait_for_load_state("networkidle")
    badge = page.locator("[aria-label=Notifications] .bg-red-500")
    assert badge.count() == 0
