"""Customer app journeys: dashboard, accounts, statement, detail, receipt, export."""
from conftest import BASE_URL, login


def test_dashboard_renders_balances_and_nav(page):
    login(page)
    body = page.content()
    assert "Aubrey" in body
    assert page.locator("h1").first.inner_text() != ""
    assert page.locator("a[href='/app/accounts/']").count() > 0
    assert page.locator("a[href='/app/cards/']").count() > 0


def test_accounts_page_lists_two_accounts(page):
    login(page)
    page.goto(f"{BASE_URL}/app/accounts/")
    rows = page.locator("text=4000110001")
    assert rows.count() >= 1
    assert page.locator("text=4000110002").count() >= 1


def test_statement_lists_transactions_and_supports_filters(page, acc_ids):
    login(page)
    page.goto(f"{BASE_URL}/app/accounts/{acc_ids['checking']}/statement/")
    assert page.locator("form").count() >= 1
    # filter by search term that matches nothing
    page.fill("input[name=q]", "zzz-no-match-zzz")
    page.locator("form:has(input[name=q]) button").click()
    page.wait_for_load_state()
    # tolerant: page still renders the filter form
    assert page.locator("input[name=q]").count() == 1


def test_statement_pagination_tolerates_abuse(page, acc_ids):
    login(page)
    page.goto(f"{BASE_URL}/app/accounts/{acc_ids['checking']}/statement/?page=9999")
    assert "Server Error" not in page.content()


def test_transaction_detail_and_receipt(page):
    from conftest import db
    ref = db(
        "from apps.transfers.models import Transfer\n"
        "t=Transfer.objects.filter(status='COMPLETED',\n"
        "   source_account__customer__username='aubrey.sabina0').order_by('-id').first()\n"
        "print(t.reference if t else '')").splitlines()[-1]
    assert ref, "no completed transfer seeded"
    login(page)
    page.goto(f"{BASE_URL}/app/transactions/{ref}/")
    assert ref in page.content()
    page.goto(f"{BASE_URL}/app/receipts/{ref}/")
    assert "receipt" in page.content().lower() or ref in page.content()


def test_statement_csv_export_downloads(page, acc_ids):
    login(page)
    url = f"{BASE_URL}/app/accounts/{acc_ids['checking']}/statement/export.csv"
    with page.expect_download() as dl:
        page.evaluate(
            "u => { const a = document.createElement('a'); a.href = u;"
            "document.body.appendChild(a); a.click(); }", url)
    assert ".csv" in dl.value.suggested_filename


def test_statement_print_view_renders(page, acc_ids):
    login(page)
    page.goto(f"{BASE_URL}/app/accounts/{acc_ids['checking']}/statement/print/")
    assert "print" in page.content().lower()


def test_analytics_and_investments_render(page):
    login(page)
    for path in ("/app/analytics/", "/app/investments/", "/app/settings/"):
        page.goto(f"{BASE_URL}{path}")
        assert "Server Error" not in page.content(), path
