"""Browser E2E baseline — Playwright against the live compose stack.

Run from the repo root on the HOST (Chromium + deps live outside the container):
    python3 -m pytest e2e_browser -q --screenshot=only-on-failure --output=e2e_browser/screenshots

Requires: docker compose up (web at http://localhost:8000) and seeded demo data
(`make seed` / seed_demo). Console errors, page errors and 5xx responses fail the test.
"""
import os
import re
import subprocess

import pytest

BASE_URL = os.environ.get("BANKIO_URL", "http://localhost:8000")

CUSTOMER = "aubrey.sabina0"
CUSTOMER_PW = "Customer!2026"
CUSTOMER2 = "liam.johnson1"
ADMIN = "admin"
MANAGER = "manager1"
STAFF_PW = "Bankio!2026"


# ---------------------------------------------------------------- helpers
def sh(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def db(query):
    """Run a python snippet inside the web container; return stdout."""
    r = sh("docker", "compose", "exec", "-T", "web",
           "python", "manage.py", "shell", "-c", query)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout.strip()


def otp_code(username, purpose=None):
    """Read the latest OTP delivered to the bankio.challenge log."""
    r = sh("docker", "compose", "logs", "web", "--tail=400")
    pat = rf"\[step-up\] (?P<purpose>[\w ]+) code for {re.escape(username)}: (\d{{6}})"
    hits = [(m.group("purpose"), m.group(2)) for m in re.finditer(pat, r.stdout)]
    if purpose:
        hits = [h for h in hits if purpose in h[0]]
    codes = [c for _, c in hits]
    assert codes, f"no otp found for {username}: {r.stdout[-500:]}"
    return codes[-1]


def login(page, username=CUSTOMER, password=CUSTOMER_PW):
    page.goto(f"{BASE_URL}/login/")
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")


def aubrey_account_ids():
    out = db(
        "from apps.accounts.models import Account\n"
        f"print(','.join(str(a.pk) for a in Account.objects.filter(customer__username='{CUSTOMER}')))")
    ids = [int(x) for x in out.split(",")]
    return {"checking": ids[0], "savings": ids[1]}


@pytest.fixture(scope="session")
def acc_ids():
    return aubrey_account_ids()


# ------------------------------------------------- console/network guards
@pytest.fixture(autouse=True)
def browser_guards(page, request):
    """Fail the test on unexpected JS console errors, page errors or 5xx."""
    problems = []

    def on_console(msg):
        if msg.type == "error":
            text = msg.text
            # resource-load failures are covered by the response listener
            # (favicon 404s etc.); don't double-report them here
            if "Failed to load resource" in text:
                return
            problems.append(f"console.error: {text}")

    def on_pageerror(err):
        problems.append(f"pageerror: {err}")

    def on_response(resp):
        if resp.status >= 500:
            problems.append(f"HTTP {resp.status}: {resp.url}")

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("response", on_response)
    yield
    # expected non-2xx (IDOR/CSRF probes) are recorded by tests via marker
    expected = getattr(request.node, "expects_http_error", False)
    real = [p for p in problems
            if not (expected and ("HTTP 4" in p or "404" in p or "403" in p
                                  or "Error Code 4" in p))]
    assert not real, "browser-level problems:\n" + "\n".join(real)


def expect_http_errors(request):
    """Mark a test as allowed to receive 4xx responses without failing."""
    request.node.expects_http_error = True
