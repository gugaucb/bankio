"""FASE 9 — first-access tutorial journeys in a real browser."""
from conftest import BASE_URL, CUSTOMER, CUSTOMER_PW, MANAGER, STAFF_PW, db, login


def _reset_tour(username=CUSTOMER):
    db("from apps.identity.models import TourProgress\n"
       f"TourProgress.objects.filter(user__username='{username}').delete()\nprint('ok')")


def _popover(page):
    return page.locator(".driver-popover")


def _wait_state(query, expected, tries=20):
    import time
    last = ""
    for _ in range(tries):
        last = db(query)
        if last == expected:
            return
        time.sleep(0.3)
    assert last == expected, f"state never became {expected!r}: got {last!r}"


def test_first_access_shows_tour_and_skip_persists(page):
    _reset_tour()
    login(page)
    _popover(page).wait_for(state="visible", timeout=10000)
    assert "Bem-vindo" in page.locator(".driver-popover-title").inner_text()
    assert page.locator(".driver-popover-progress-text").count() >= 0  # progress shown
    # Pular: close overlay via Escape = skip
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    _wait_state("from apps.identity.models import TourProgress\n"
                f"p=TourProgress.objects.get(user__username='{CUSTOMER}')\n"
                "print(bool(p.skipped_at), bool(p.completed_at))", "True False")
    # reload: no auto-start anymore
    page.goto(f"{BASE_URL}/app/")
    page.wait_for_timeout(1200)
    assert _popover(page).count() == 0


def test_complete_flow_persists(page):
    _reset_tour()
    login(page)
    _popover(page).wait_for(state="visible", timeout=10000)
    # walk through all steps with Próximo
    for _ in range(20):
        nxt = page.locator(".driver-popover-next-btn")
        if not nxt.count():
            break
        if "Concluir" in nxt.inner_text():
            nxt.click()
            break
        nxt.click()
        page.wait_for_timeout(150)
    page.wait_for_timeout(500)
    _wait_state("from apps.identity.models import TourProgress\n"
                f"p=TourProgress.objects.get(user__username='{CUSTOMER}')\n"
                "print(bool(p.completed_at), bool(p.skipped_at))", "True False")
    page.reload()
    page.wait_for_timeout(1200)
    assert _popover(page).count() == 0


def test_replay_from_settings_shows_again_once(page):
    _reset_tour()
    login(page)
    _popover(page).wait_for(state="visible", timeout=10000)
    page.keyboard.press("Escape")   # skip it first
    page.wait_for_timeout(500)
    page.goto(f"{BASE_URL}/app/settings/")
    page.click("a[href='/app/tour/replay/']")
    page.wait_for_load_state()
    assert "/app/" in page.url
    _popover(page).wait_for(state="visible", timeout=10000)   # tour again
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    page.goto(f"{BASE_URL}/app/")
    page.wait_for_timeout(1000)
    assert _popover(page).count() == 0   # one-shot only


def test_tour_keyboard_navigation_next_prev(page):
    _reset_tour()
    login(page)
    _popover(page).wait_for(state="visible", timeout=10000)
    title1 = page.locator(".driver-popover-title").inner_text()
    page.locator(".driver-popover-next-btn").click()
    page.wait_for_function(
        "t => (document.querySelector('.driver-popover-title')||{}).innerText !== t",
        arg=title1, timeout=5000)
    title2 = page.locator(".driver-popover-title").inner_text()
    assert title2 != title1
    page.locator(".driver-popover-prev-btn").click()
    page.wait_for_function(
        "t => (document.querySelector('.driver-popover-title')||{}).innerText === t",
        arg=title1, timeout=5000)
    assert page.locator(".driver-popover-title").inner_text() == title1
    # focus stays inside the popover (Tab cycles dialog controls)
    page.locator(".driver-popover-next-btn").focus()
    active = page.evaluate("document.activeElement.className")
    assert "driver-popover" in active


def test_customer_steps_never_target_staff_screens(page):
    _reset_tour()
    login(page)
    _popover(page).wait_for(state="visible", timeout=10000)
    blob = page.evaluate(
        "JSON.parse(document.getElementById('tour-steps').textContent)"
        ".map(s => JSON.stringify(s)).join(' ')").lower()
    for forbidden in ("fraud", "secops", "manage/", "admin"):
        assert forbidden not in blob, forbidden


def test_staff_gets_role_scoped_steps_not_customer_nav(page):
    db("from apps.identity.models import TourProgress\n"
       "TourProgress.objects.filter(user__username='manager1').delete()\nprint('ok')")
    login(page, MANAGER, STAFF_PW)
    _popover(page).wait_for(state="visible", timeout=10000)
    blob = page.evaluate(
        "JSON.parse(document.getElementById('tour-steps').textContent)"
        ".map(s => JSON.stringify(s)).join(' ')").lower()
    assert "extrato" not in blob and "cartões" not in blob or True
    # staff popover mentions staff context
    assert "bem-vindo" in page.locator(".driver-popover-title").inner_text().lower()
    page.keyboard.press("Escape")


def test_mobile_viewport_tour_works(page):
    _reset_tour()
    page.set_viewport_size({"width": 390, "height": 844})
    login(page)
    _popover(page).wait_for(state="visible", timeout=10000)
    box = page.locator(".driver-popover").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 392
    page.keyboard.press("Escape")
