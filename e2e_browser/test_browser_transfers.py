"""Transfer journeys via real HTMX interactions on /transfers/."""
from conftest import BASE_URL, CUSTOMER, login


def _wait_result(page):
    page.wait_for_function(
        "document.querySelector('#transfer-result').innerText.trim() !== ''",
        timeout=15000)


def _destination_number():
    """DEFECT #1 FIXED: numeric destinations resolve by account_number first,
    falling back to pk — the journey types the real account number."""
    from conftest import db
    return db(
        "from apps.accounts.models import Account\n"
        "a=Account.objects.filter(customer__username='liam.johnson1').first()\n"
        "print(a.account_number)").splitlines()[-1]


def test_internal_transfer_via_htmx(page):
    from conftest import db
    dst = _destination_number()
    before = db("from apps.accounts.models import Account\n"
                "la=Account.objects.get(account_number='%s').ledger_account\n"
                "print(sum((l.amount if l.side=='DEBIT' else -l.amount) for l in la.entries.all()))"
                % dst)
    login(page)
    page.goto(f"{BASE_URL}/transfers/")
    page.select_option("select[name=source_account]", index=0)
    page.fill("input[name=destination_account]", dst)
    page.fill("input[name=amount]", "12.34")
    page.fill("input[name=description]", "browser e2e transfer")
    page.click("#transfer-form .btn-primary")
    _wait_result(page)
    result = page.locator("#transfer-result").inner_text().lower()
    assert "completed" in result or "success" in result, result
    after = db("from apps.accounts.models import Account\n"
               "la=Account.objects.get(account_number='%s').ledger_account\n"
               "print(sum((l.amount if l.side=='DEBIT' else -l.amount) for l in la.entries.all()))"
               % dst)
    assert after != before  # destination ledger moved


def _allow_htmx_4xx_swap(page):
    """htmx ignores 4xx bodies by default; enable swap so the DOM shows errors."""
    page.evaluate("document.body.addEventListener('htmx:beforeSwap', "
                  "e => { if (e.detail.xhr.status >= 400) e.detail.shouldSwap = true; })")


def test_transfer_insufficient_funds_shows_error(page, request):
    from conftest import expect_http_errors
    expect_http_errors(request)  # htmx logs the 400 to console.error
    # shrink the checking balance below the $5000 tx-limit band so an
    # above-balance amount reaches INSUFFICIENT_FUNDS instead of TX_LIMIT
    from conftest import db
    db("from apps.accounts.models import Account\n"
       "from apps.ledger import services as ledger\n"
       "from decimal import Decimal\n"
       "la=Account.objects.get(account_number='4000110001').ledger_account\n"
       "eq=ledger.get_or_create_account('3900-OPENING-EQUITY','Opening Balances',type='EQUITY')\n"
       "bal=ledger.account_balance(la)\n"
       "if bal>Decimal('500'):\n"
       "    ledger.post_journal(reference=f'E2E-SHRINK-{bal}', description='e2e',\n"
       "                        lines=[(la,'DEBIT',bal-Decimal('300')),(eq,'CREDIT',bal-Decimal('300'))])\n"
       "print('ok')")
    login(page)
    page.goto(f"{BASE_URL}/transfers/")
    _allow_htmx_4xx_swap(page)
    page.select_option("select[name=source_account]", index=0)
    page.fill("input[name=destination_account]", _destination_number())
    page.fill("input[name=amount]", "900.00")  # <= tx limit, > shrunken balance
    page.click("#transfer-form .btn-primary")
    _wait_result(page)
    result = page.locator("#transfer-result").inner_text().lower()
    assert "insufficient" in result or "funds" in result, result


def test_transfer_invalid_amount_rejected(page):
    """Negative amount is stopped by the HTML5 constraint (min=0.01) — the
    browser itself blocks the submit (client-side defense in depth)."""
    login(page)
    page.goto(f"{BASE_URL}/transfers/")
    _allow_htmx_4xx_swap(page)
    page.select_option("select[name=source_account]", index=0)
    amount = page.locator("input[name=amount]")
    amount.fill("-5.00")
    validity = amount.evaluate("el => el.validity.valid")
    assert validity is False, "browser allowed a negative amount"


def test_transfer_history_lists_references(page):
    login(page)
    page.goto(f"{BASE_URL}/transfers/")
    body = page.content()
    assert "TRF-" in body


def test_external_beneficiary_transfer_via_htmx(page):
    """External beneficiary (Netflix-style) — external settlement through clearing."""
    from conftest import db
    login(page)
    page.goto(f"{BASE_URL}/transfers/")
    page.select_option("select[name=source_account]", index=0)
    ben_value = page.locator(
        "select[name=beneficiary] option", has_text="John Doe").get_attribute("value")
    if not ben_value:
        ben_value = page.locator(
            "select[name=beneficiary] option[value!='']").last.get_attribute("value")
    page.select_option("select[name=beneficiary]", ben_value)
    page.fill("input[name=amount]", "5.00")
    page.fill("input[name=description]", "browser external e2e")
    page.click("#transfer-form .btn-primary")
    _wait_result(page)
    result = page.locator("#transfer-result").inner_text().lower()
    assert ("completed" in result) or ("success" in result) or ("pending" in result), result
