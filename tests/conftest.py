import pytest
from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.accounts.models import Account
from apps.customers.models import Customer
from apps.ledger import services as ledger


@pytest.fixture(autouse=True)
def _enable_db_access_for_all(db):
    pass


def make_user(username, role="CUSTOMER", password="Test!12345", **kw):
    User = get_user_model()
    return User.objects.create_user(username=username, email=f"{username}@t.io",
                                    password=password, role=role,
                                    first_name=username.capitalize(), **kw)


@pytest.fixture
def user_factory(db):
    return make_user


@pytest.fixture
def aubrey(user_factory):
    u = make_user("aubrey")
    Customer.objects.create(user=u, customer_number="CUST-T1")
    return u


def open_account(user, balance, number, type_="CHECKING"):
    la = ledger.get_or_create_account(f"2001-{number}", f"Account {number}", is_customer=True)
    acct = Account.objects.create(customer=user, account_number=number, ledger_account=la, type=type_)
    if Decimal(str(balance)) > 0:
        equity = ledger.get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        ledger.post_journal(reference=f"OPEN-{number}", description="opening",
                            lines=[(equity, "DEBIT", Decimal(str(balance))), (la, "CREDIT", Decimal(str(balance)))])
    return acct


@pytest.fixture
def account_factory(db):
    def _f(user, balance="1000.00", number=None):
        number = number or f"9{Account.objects.count() + 1:09d}"
        return open_account(user, balance, number)
    return _f


@pytest.fixture
def alice(user_factory, account_factory):
    u = make_user("alice")
    Customer.objects.create(user=u, customer_number="CUST-TA")
    u.checking = account_factory(u, "1000.00")
    return u


@pytest.fixture
def bob(user_factory, account_factory):
    u = make_user("bob")
    Customer.objects.create(user=u, customer_number="CUST-T2")
    u.checking = account_factory(u, "500.00")
    return u
