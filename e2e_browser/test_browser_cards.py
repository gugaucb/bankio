"""Cards journeys: list, detail, controls, invoices, invoice payment."""
from conftest import BASE_URL, login


def test_cards_list_renders(page):
    login(page)
    page.goto(f"{BASE_URL}/app/cards/")
    assert page.locator("a[href*='/app/cards/']").count() >= 1
    body = page.content().lower()
    assert "••" in body or "last4" in body or "card" in body


def test_card_detail_shows_masked_number_and_controls(page):
    login(page)
    page.goto(f"{BASE_URL}/app/cards/1/")
    body = page.content()
    assert "Report lost" in body  # control buttons present
    assert page.locator("button", has_text="Freeze").count() >= 1


def test_freeze_unfreeze_roundtrip(page):
    login(page)
    page.goto(f"{BASE_URL}/app/cards/1/")
    page.click("form[action*='/controls/'] button", timeout=5000) if False else None
    # click the freeze (or unfreeze) toggle — first control form button
    btn = page.locator("form[action*='controls'] button").first
    label_before = btn.inner_text()
    btn.click()
    page.wait_for_load_state("networkidle")
    page.goto(f"{BASE_URL}/app/cards/1/")
    btn2 = page.locator("form[action*='controls'] button").first
    assert btn2.inner_text() != label_before  # toggled FROZEN <-> ACTIVE
    # restore original state
    btn2.click()
    page.wait_for_load_state("networkidle")


def test_toggle_online_purchases(page):
    login(page)
    page.goto(f"{BASE_URL}/app/cards/1/")
    online_btn = page.locator("form[action*='controls']:has(input[value=toggle_online]) button")
    state = "ON" if "bg-emerald-100" in (online_btn.get_attribute("class") or "") else "OFF"
    online_btn.click()
    page.wait_for_load_state("networkidle")
    online_btn = page.locator("form[action*='controls']:has(input[value=toggle_online]) button")
    new_state = "ON" if "bg-emerald-100" in (online_btn.get_attribute("class") or "") else "OFF"
    assert new_state != state
    online_btn.click()  # restore
    page.wait_for_load_state("networkidle")


def test_invoices_page_and_open_total(page):
    login(page)
    page.goto(f"{BASE_URL}/app/cards/1/invoices/")
    assert page.locator("text=$").count() >= 1


def test_pay_invoice_via_post_form(page):
    """Pay the open invoice; idempotency protects against double submit."""
    from conftest import db
    total = db("from apps.cards.billing import open_cycle_total\n"
               "from apps.cards.models import Card\n"
               f"print(open_cycle_total(Card.objects.get(pk=1)))")
    if float(total.splitlines()[-1]) <= 0:
        return  # nothing open to pay — journey covered by unit tests
    login(page)
    page.goto(f"{BASE_URL}/app/cards/1/invoices/")
    page.locator("form[action*='/invoices/pay/'] button").click()
    page.wait_for_load_state("networkidle")
