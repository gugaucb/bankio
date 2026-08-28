"""Account funding: ledger-only inflow, idempotent replay, RBAC and validation."""
from decimal import Decimal

import pytest
from django.test import Client

from apps.accounts.models import Account, AccountStatus
from apps.accounts.services import FundingError, fund_account
from apps.audit.models import AuditLog
from apps.ledger.models import JournalEntry, LedgerEntry
from apps.managerops.models import BankBranch, ManagerProfile
from tests.conftest import make_user

KEY = "fund-test-001"


@pytest.fixture
def funding_scene(db):
    branch = BankBranch.objects.create(branch_code="5001", name="Fund North", region="NORTH")
    mgr = make_user("fund_mgr", role="MANAGER", password="Mgr!12345")
    ManagerProfile.objects.create(user=mgr, level="BRANCH_MANAGER", branch=branch)
    cust = make_user("fund_cust", password="Test!12345")
    from apps.customers.models import Customer

    Customer.objects.create(user=cust, customer_number="CUST-FUND1", branch=branch)
    from apps.accounts.services import open_account

    acct = open_account(customer=cust, type="CHECKING", currency="USD")
    return {"mgr": mgr, "cust": cust, "acct": acct}


def balance(acct):
    acct.refresh_from_db()
    return acct.current_balance


@pytest.mark.django_db
def test_funding_posts_balanced_journal_and_raises_balance(funding_scene):
    mgr, acct = funding_scene["mgr"], funding_scene["acct"]
    before = balance(acct)
    result = fund_account(manager=mgr, account_id=acct.pk, amount="250.50",
                          reason="Cash deposit", idempotency_key=KEY)
    assert not result["replayed"]
    j = result["journal"]
    assert j.status == "POSTED"
    debits, credits = j.balance_check()
    assert debits == credits == Decimal("250.50")
    assert balance(acct) == before + Decimal("250.50")


@pytest.mark.django_db
def test_funding_replay_does_not_create_money(funding_scene):
    mgr, acct = funding_scene["mgr"], funding_scene["acct"]
    fund_account(manager=mgr, account_id=acct.pk, amount="100", idempotency_key=KEY)
    b1 = balance(acct)
    r2 = fund_account(manager=mgr, account_id=acct.pk, amount="100", idempotency_key=KEY)
    assert r2["replayed"]
    assert balance(acct) == b1
    assert JournalEntry.objects.filter(reference__startswith="FUND-").count() == 1


@pytest.mark.django_db
def test_funding_validation(funding_scene):
    mgr, acct = funding_scene["mgr"], funding_scene["acct"]
    for bad in ("0", "-10", "abc"):
        with pytest.raises(FundingError) as e:
            fund_account(manager=mgr, account_id=acct.pk, amount=bad, idempotency_key=f"k-{bad}")
        assert e.value.code == "INVALID_AMOUNT"
    with pytest.raises(FundingError) as e:
        fund_account(manager=mgr, account_id=999999, amount="10", idempotency_key="k-miss")
    assert e.value.code == "ACCOUNT_NOT_FOUND"
    # no journals posted for failed attempts
    assert not JournalEntry.objects.filter(reference__startswith="FUND-").exists()


@pytest.mark.django_db
def test_funding_blocked_account_refused(funding_scene):
    mgr, acct = funding_scene["mgr"], funding_scene["acct"]
    acct.status = AccountStatus.BLOCKED
    acct.save()
    with pytest.raises(FundingError) as e:
        fund_account(manager=mgr, account_id=acct.pk, amount="10", idempotency_key="k-blk")
    assert e.value.code == "ACCOUNT_NOT_ACTIVE"


@pytest.mark.django_db
def test_funding_audited(funding_scene):
    mgr, acct = funding_scene["mgr"], funding_scene["acct"]
    fund_account(manager=mgr, account_id=acct.pk, amount="75", idempotency_key=KEY)
    assert AuditLog.objects.filter(action="FUNDING_EXECUTED").exists()


@pytest.mark.django_db
def test_customer_cannot_use_funding_view(funding_scene):
    c = Client(enforce_csrf_checks=False)
    c.force_login(funding_scene["cust"])
    assert c.get("/manage/funding/").status_code == 403
    assert c.post("/manage/funding/", {"account": funding_scene["acct"].pk,
                                       "amount": "10", "idempotency_key": "x1"}).status_code == 403


@pytest.mark.django_db
def test_funding_view_posts_and_shows(funding_scene):
    c = Client(enforce_csrf_checks=False)
    c.force_login(funding_scene["mgr"])
    r = c.get("/manage/funding/")
    assert r.status_code == 200
    assert funding_scene["acct"].account_number.encode() in r.content
    r = c.post("/manage/funding/", {"account": funding_scene["acct"].pk, "amount": "40.00",
                                    "idempotency_key": KEY, "reason": "Branch deposit"})
    assert r.status_code == 302
    assert balance(funding_scene["acct"]) == Decimal("40.00")
    # replay via same key
    c.post("/manage/funding/", {"account": funding_scene["acct"].pk, "amount": "40.00",
                                "idempotency_key": KEY})
    assert balance(funding_scene["acct"]) == Decimal("40.00")
