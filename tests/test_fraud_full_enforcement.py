"""Task 39: full enforcement — engine gates card purchases and bill payments."""
import pytest
from decimal import Decimal
from uuid import uuid4

from apps.accounts.models import Account
from apps.cards.services import CardDeclined, purchase
from apps.fraud import modes
from apps.fraud.models import FraudEngineSetting, RiskEvaluation, RiskRule
from apps.ledger.models import JournalEntry
from apps.payments.models import Bill
from apps.payments.services import PaymentError, pay_bill


@pytest.fixture(autouse=True)
def clean(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    FraudEngineSetting.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    RiskRule.objects.all().delete()
    yield
    FraudEngineSetting.objects.all().delete()


@pytest.fixture
def manager(django_user_model):
    return django_user_model.objects.create_user(
        "fe-mgr", email="fem@t.io", password="x", role="FRAUD_MANAGER")


@pytest.fixture
def funded_account(db, django_user_model):
    from apps.ledger import services as ledger

    user = django_user_model.objects.create_user("fe-user", email="fe@t.io", password="x")
    cash = ledger.get_or_create_account(f"FE-CASH-{uuid4().hex[:6]}", "Cash", type="ASSET")
    la = ledger.get_or_create_account(f"2001-FE-{user.username}", f"Deposit {user.username}",
                                      is_customer=True)
    ledger.post_journal(f"FE-DEP-{uuid4().hex[:8]}", "dep",
                        [(cash, "DEBIT", Decimal("5000.00")), (la, "CREDIT", Decimal("5000.00"))])
    acct = Account.objects.create(customer=user, account_number=f"55{user.pk:010d}", ledger_account=la)
    return user, acct


@pytest.mark.django_db
def test_bill_payment_blocked_in_enforcement(funded_account, manager):
    modes.set_mode(RiskEvaluation.EngineMode.ENFORCEMENT, actor=manager)
    user, acct = funded_account
    bill = Bill.objects.create(biller="EVIL Utility", amount=Decimal("100.00"))
    RiskRule.objects.create(rule_id="FE-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    key = f"FE-PAY-{uuid4().hex[:8]}"
    with pytest.raises(PaymentError) as e:
        pay_bill(actor=user, account_id=acct.pk, bill_id=bill.pk, idempotency_key=key)
    assert e.value.args[0] == "RISK_BLOCKED"
    assert not JournalEntry.objects.filter(reference__contains=key).exists()


@pytest.mark.django_db
def test_card_purchase_declined_in_enforcement(db, django_user_model, manager):
    from apps.accounts.models import AccountStatus
    from apps.cards.models import Card, CardStatus, CardType
    from apps.ledger import services as ledger

    modes.set_mode(RiskEvaluation.EngineMode.ENFORCEMENT, actor=manager)
    user = django_user_model.objects.create_user("fe-card", email="fc@t.io", password="x")
    la = ledger.get_or_create_account(f"2001-FEC-{user.username}", "dep", is_customer=True)
    acct = Account.objects.create(customer=user, account_number=f"56{user.pk:010d}",
                                  status=AccountStatus.ACTIVE, ledger_account=la)
    card = Card.objects.create(account=acct, type=CardType.DEBIT,
                               status=CardStatus.ACTIVE, last4="4242",
                               holder_name=user.username)
    RiskRule.objects.create(rule_id="FE-CARD-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    with pytest.raises(CardDeclined) as e:
        purchase(card_id=card.pk, merchant="Shady Shop", amount_raw="50.00")
    assert e.value.args[0] in ("RISK_BLOCKED", "STEP_UP_REQUIRED")


@pytest.mark.django_db
def test_shadow_still_never_blocks_payment(funded_account):
    user, acct = funded_account
    bill = Bill.objects.create(biller="Normal Utility", amount=Decimal("40.00"))
    RiskRule.objects.create(rule_id="FE-SHADOW-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    payment, created = pay_bill(actor=user, account_id=acct.pk, bill_id=bill.pk,
                                idempotency_key=f"FE-SH-{uuid4().hex[:8]}")
    assert created and payment.status == "COMPLETED"
