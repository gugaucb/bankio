"""Capture README screenshots from the running local app (seed_demo data).

Usage: app up on http://localhost:8000, then `python docs/capture_screenshots.py`.
Outputs PNGs (1280x800 viewport) into docs/screenshots/.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8000"
OUT = Path(__file__).parent / "screenshots"

CUSTOMER = ("aubrey.sabina0", "Customer!2026")
MANAGER = ("manager1", "Bankio!2026")
ADMIN = ("admin", "Bankio!2026")

SHOTS = {
    "customer-dashboard.png": ("/app/", CUSTOMER),
    "customer-transfers.png": ("/transfers/", CUSTOMER),
    "customer-cards.png": ("/app/cards/", CUSTOMER),
    "customer-security.png": ("/app/security/", CUSTOMER),
    "manager-portal.png": ("/manage/", MANAGER),
    "admin-users.png": ("/manage/users/", ADMIN),
}


def _skip_tour(page):
    """Fresh users get the Driver.js tour; dismiss it so it doesn't overlay."""
    if page.locator(".driver-popover").count():
        page.keyboard.press("Escape")


def login(page, username, password):
    page.goto(f"{BASE_URL}/login/")
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, (path, (user, pw)) in SHOTS.items():
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            login(page, user, pw)
            _skip_tour(page)
            page.goto(f"{BASE_URL}{path}")
            page.wait_for_load_state("networkidle")
            _skip_tour(page)
            page.screenshot(path=str(OUT / name))
            print("saved", OUT / name)
            ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
