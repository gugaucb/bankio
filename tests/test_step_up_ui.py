"""Branch 1 — customer-facing step-up challenge surface + code delivery.

The challenge backend is reused as-is; what's under test here is the
HTTP surface (GET presents / POST validates), IDOR/CSRF/no-leak contract,
and the out-of-band delivery path.
"""
from decimal import Decimal
from uuid import uuid4

import pytest
from django.test import Client

from apps.audit.models import AuditLog
from apps.fraud.challenge_delivery import issue_and_deliver
from apps.fraud.models import FraudEngineSetting, RiskChallenge, RiskEvaluation, RiskRule
from apps.notifications.models import Notification
from tests.conftest import make_user


@pytest.fixture
def accounts_setup(db, django_user_model):
    """Sender + receiver with funded ledger-backed accounts."""
    from decimal import Decimal as D

    from apps.accounts.models import Account
    from apps.ledger import services as ledger

    sender = django_user_model.objects.create_user("su-sender", email="sus@t.io", password="x")
    receiver = django_user_model.objects.create_user("su-receiver", email="sur@t.io", password="x")
    cash = ledger.get_or_create_account(f"SU-CASH-{uuid4().hex[:6]}", "Cash", type="ASSET")
    src = dst = None
    for user, amount in ((sender, "5000.00"), (receiver, "100.00")):
        la = ledger.get_or_create_account(
            f"2001-SU-{user.username}", f"Deposit {user.username}", is_customer=True)
        acct = Account.objects.create(customer=user, account_number=f"87{user.pk:010d}",
                                      ledger_account=la)
        ledger.post_journal(
            f"SU-DEP-{uuid4().hex[:8]}", "dep",
            [(cash, "DEBIT", D(amount)), (la, "CREDIT", D(amount))],
        )
        if user is sender:
            src = acct
        else:
            dst = acct
    return sender, receiver, src, dst


@pytest.fixture(autouse=True)
def clean_engine(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    FraudEngineSetting.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield
    FraudEngineSetting.objects.all().delete()


@pytest.fixture
def customer(db):
    return make_user("ch-customer", password="Ch!12345678")


@pytest.fixture
def other(db):
    return make_user("ch-other", password="Ch!12345678")


def _evaluation(customer, **kw):
    return RiskEvaluation.objects.create(
        operation_type="TRANSFER", customer=customer, actor=customer,
        amount=Decimal("10.00"), currency="USD", engine_mode="CHALLENGE_ONLY",
        status=RiskEvaluation.Status.COMPLETED, decision=RiskEvaluation.Decision.BLOCK,
        idempotency_key=kw.get("key", "k-test"),
    )


FACTS = {"amount": "10.00", "beneficiary": "7", "idempotency_key": "k-test"}


def _challenge(customer):
    """Issue through the production delivery path (notification + audit)."""
    return issue_and_deliver(_evaluation(customer), customer, FACTS)


def _c(user=None):
    c = Client(enforce_csrf_checks=True)
    if user is not None:
        c = Client()
        c.force_login(user)
    return c


# ---------------------------------------------------------------- surface

@pytest.mark.django_db
def test_get_presents_form_without_secrets(customer):
    ch, _code = _challenge(customer)
    r = _c(customer).get(f"/security/challenge/{ch.pk}/")
    assert r.status_code == 200
    body = r.content.decode()
    assert 'name="code"' in body and "Confirm code" in body
    assert "USD 10.00" in body                       # minimal context only
    for secret in (_code, ch.code_hash, ch.material_hash):
        assert secret not in body                    # never leaks to the client


@pytest.mark.django_db
def test_anonymous_redirected(client):
    r = client.get("/security/challenge/1/")
    assert r.status_code == 302 and "/login" in r["Location"]


@pytest.mark.django_db
def test_missing_challenge_is_404(customer):
    assert _c(customer).get("/security/challenge/999999/").status_code == 404


@pytest.mark.django_db
def test_idor_other_users_challenge_is_404(customer, other):
    ch, _code = _challenge(customer)
    # another customer cannot even learn it exists
    assert _c(other).get(f"/security/challenge/{ch.pk}/").status_code == 404
    assert _c(other).post(f"/security/challenge/{ch.pk}/",
                          {"code": "123456"}).status_code == 404


@pytest.mark.django_db
def test_csrf_required_on_post(customer):
    ch, _code = _challenge(customer)
    c = Client(enforce_csrf_checks=True)
    c.force_login(customer)
    r = c.post(f"/security/challenge/{ch.pk}/",
               {"code": "000000", **{f"fact_{k}": v for k, v in FACTS.items()}})
    assert r.status_code == 403
    ch.refresh_from_db()
    assert ch.status == RiskChallenge.Status.PENDING   # untouched


# ------------------------------------------------------------- validation

def _facts_payload(**overrides):
    facts = {**FACTS, **overrides}
    return {f"fact_{k}": v for k, v in facts.items()}


@pytest.mark.django_db
def test_post_correct_code_verifies(customer):
    ch, code = _challenge(customer)
    r = _c(customer).post(f"/security/challenge/{ch.pk}/",
                          {"code": code, **_facts_payload()})
    assert r.status_code == 302
    ch.refresh_from_db()
    assert ch.status == RiskChallenge.Status.VERIFIED and ch.verified_at


@pytest.mark.django_db
def test_wrong_code_rejected_still_pending(customer):
    ch, real = _challenge(customer)
    r = _c(customer).post(f"/security/challenge/{ch.pk}/",
                          {"code": "000000", **_facts_payload()})
    assert r.status_code == 400 and "Invalid code" in r.content.decode()
    ch.refresh_from_db()
    assert ch.status == RiskChallenge.Status.PENDING


@pytest.mark.django_db
def test_post_without_facts_rejected(customer):
    ch, code = _challenge(customer)
    r = _c(customer).post(f"/security/challenge/{ch.pk}/", {"code": code})
    assert r.status_code == 400 and "Missing operation context" in r.content.decode()
    ch.refresh_from_db()
    assert ch.status == RiskChallenge.Status.PENDING


@pytest.mark.django_db
def test_tampered_material_invalidates_challenge(customer):
    """Changing a fact after issuance must kill the challenge — no verify."""
    ch, code = _challenge(customer)
    r = _c(customer).post(f"/security/challenge/{ch.pk}/",
                          {"code": code, **_facts_payload(amount="9999.00")})
    assert r.status_code == 400 and "invalidated" in r.content.decode()
    ch.refresh_from_db()
    assert ch.status == RiskChallenge.Status.EXPIRED


@pytest.mark.django_db
def test_expired_challenge_shows_state_message(customer):
    from django.utils import timezone

    ch, _code = _challenge(customer)
    RiskChallenge.objects.filter(pk=ch.pk).update(
        expires_at=timezone.now() - timezone.timedelta(minutes=1))
    r = _c(customer).get(f"/security/challenge/{ch.pk}/")
    assert b"This challenge has expired" in r.content
    r = _c(customer).post(f"/security/challenge/{ch.pk}/",
                          {"code": "123456", **_facts_payload()})
    assert b"expired" in r.content


@pytest.mark.django_db
def test_consumed_challenge_shows_state_message(customer):
    from apps.fraud.challenge import consume_challenge, verify_challenge

    ch, code = _challenge(customer)
    verify_challenge(ch, code, FACTS)
    consume_challenge(ch, "TRANSFER:k-test")
    r = _c(customer).get(f"/security/challenge/{ch.pk}/")
    assert b"already used" in r.content
    r = _c(customer).post(f"/security/challenge/{ch.pk}/",
                          {"code": code, **_facts_payload()})
    assert b"already used" in r.content            # replay via UI fails closed


@pytest.mark.django_db
def test_verified_challenge_replay_fails(customer):
    ch, code = _challenge(customer)
    url = f"/security/challenge/{ch.pk}/"
    payload = {"code": code, **_facts_payload()}
    c = _c(customer)
    assert c.post(url, payload).status_code == 302     # first verify ok
    r = c.post(url, payload)                           # double submit
    assert r.status_code == 400 and b"already used" in r.content


@pytest.mark.django_db
def test_unknown_fact_keys_are_dropped(customer):
    """Only whitelisted fact fields participate in the digest."""
    ch, code = _challenge(customer)
    payload = {"code": code, **_facts_payload(),
               "fact_evil": "1", "is_admin": "True"}
    r = _c(customer).post(f"/security/challenge/{ch.pk}/", payload)
    assert r.status_code == 302                        # verified despite junk fields


# --------------------------------------------------------------- delivery

@pytest.mark.django_db
def test_issue_and_deliver_notification_without_code(customer):
    ch, code = _challenge(customer)
    note = Notification.objects.get(recipient=customer, category="SECURITY")
    assert "verification code" in note.body.lower()
    assert code not in note.body and code not in note.title


@pytest.mark.django_db
def test_issuance_audited_without_secrets(customer):
    ch, code = _challenge(customer)
    ev = AuditLog.objects.filter(action="CHALLENGE_ISSUED", resource_id=str(ch.pk)).latest("pk")
    meta_full = str(ev.metadata) + str(ev.__dict__.values())
    for secret in (code, ch.code_hash, ch.material_hash):
        assert str(secret) not in meta_full
    assert ev.metadata["operation_type"] == "TRANSFER"


@pytest.mark.django_db
def test_transfer_step_up_delivers_out_of_band(accounts_setup, caplog):
    """End-to-end: CHALLENGE_ONLY transfer stops with STEP_UP_REQUIRED and
    delivers its code only through the simulated out-of-band channel."""
    import logging

    sender, _receiver, src, dst = accounts_setup
    RiskRule.objects.create(rule_id="SU-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    mgr = make_user("su-mgr", role="FRAUD_MANAGER")
    from apps.fraud import modes

    modes.set_mode(RiskEvaluation.EngineMode.CHALLENGE_ONLY, actor=mgr)

    key = f"SU-{uuid4().hex[:10]}"
    from apps.transfers.services import TransferError, execute_transfer

    with caplog.at_level(logging.INFO, logger="bankio.challenge"):
        with pytest.raises(TransferError) as e:
            execute_transfer(actor=sender, source_account_id=src.pk,
                             amount=Decimal("10.00"),
                             destination_account_id=dst.pk, idempotency_key=key)
    assert e.value.code == "STEP_UP_REQUIRED"
    ch = RiskChallenge.objects.latest("pk")
    assert e.value.challenge_id == ch.pk

    from apps.fraud.challenge import material_hash as mh

    code = next(m.split(": ")[-1].split()[0] for m in caplog.messages if "step-up" in m)
    assert mh(code)[:32] == ch.code_hash          # delivered code matches issuance

    # customer got an in-app heads-up without any code
    note = Notification.objects.get(recipient=sender, category="SECURITY")
    assert "verification code was sent" in note.body.lower()
    assert code not in note.body
