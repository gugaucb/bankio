"""Branch 2 — safe transfer resume after a step-up challenge.

Contract under test:
  TRANSFER → CHALLENGE issued → STEP_UP_REQUIRED
  → correct code → locked verify+consume → settlement EXACTLY ONCE
  Any material change / wrong code / replay → zero financial movement.
"""
import logging
import threading
from decimal import Decimal
from uuid import uuid4

import pytest
from django.test import Client

from apps.fraud import modes
from apps.fraud.models import FraudEngineSetting, RiskChallenge, RiskEvaluation, RiskRule
from apps.ledger.models import JournalEntry
from apps.ledger.services import account_balance
from tests.conftest import make_user


@pytest.fixture(autouse=True)
def clean_engine(db, settings):
    settings.FRAUD_MODE = "SHADOW"
    FraudEngineSetting.objects.all().delete()
    RiskEvaluation.objects.all().delete()
    yield
    FraudEngineSetting.objects.all().delete()


@pytest.fixture(autouse=True)
def oob_capture():
    """Capture the simulated out-of-band channel (dev stand-in for SMS/email).

    Tests read codes exactly where a real gateway integration would read them.
    """
    class _Sink(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.INFO)
            self.records = []

        def emit(self, record):
            self.records.append(record)

    sink = _Sink()
    logger = logging.getLogger("bankio.challenge")
    logger.addHandler(sink)
    logger.setLevel(logging.INFO)
    yield sink
    logger.removeHandler(sink)


def _delivered_code(sink, challenge_id):
    for record in sink.records:
        msg = record.getMessage()
        if f"CHL-{challenge_id} " in msg:
            return msg.rsplit(": ", 1)[1].split()[0]
    raise AssertionError(f"no delivered code found for challenge {challenge_id}")


@pytest.fixture
def challenged(accounts_setup):
    """Sender/receiver in CHALLENGE_ONLY mode with a score-100 rule active."""
    sender, receiver, src, dst = accounts_setup
    RiskRule.objects.create(rule_id="RES-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    mgr = make_user("res-mgr", role="FRAUD_MANAGER")
    modes.set_mode(RiskEvaluation.EngineMode.CHALLENGE_ONLY, actor=mgr)
    return sender, receiver, src, dst


def _start_transfer(sender, src, dst, key):
    from apps.transfers.services import TransferError, execute_transfer

    try:
        return execute_transfer(actor=sender, source_account_id=src.pk,
                                amount=Decimal("10.00"),
                                destination_account_id=dst.pk,
                                idempotency_key=key, description="resume me")
    except TransferError as e:
        assert e.code == "STEP_UP_REQUIRED"
        return e


# ------------------------------------------------------------ service flow

@pytest.mark.django_db
def test_resume_completes_settlement_exactly_once(challenged, oob_capture):
    from apps.transfers.models import Transfer
    from apps.transfers.services import execute_transfer, resume_transfer

    sender, receiver, src, dst = challenged
    key = f"RS-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    code = _delivered_code(oob_capture, err.challenge_id)
    bal_src, bal_dst = account_balance(src.ledger_account), account_balance(dst.ledger_account)

    transfer, created = resume_transfer(
        actor=sender, challenge_id=err.challenge_id, code=code,
        facts=dict(err.facts), description="resume me")
    assert created is True and transfer.status == "COMPLETED"
    assert transfer.description == "resume me"
    ch = RiskChallenge.objects.get(pk=err.challenge_id)
    assert ch.status == RiskChallenge.Status.CONSUMED
    # money moved exactly once: 10.00 left source, 10.00 arrived at destination
    assert account_balance(src.ledger_account) == bal_src - Decimal("10.00")
    assert account_balance(dst.ledger_account) == bal_dst + Decimal("10.00")
    # replay of the same idempotency key never settles again
    t2, created2 = execute_transfer(actor=sender, source_account_id=src.pk,
                                    amount=Decimal("10.00"),
                                    destination_account_id=dst.pk,
                                    idempotency_key=key)
    assert created2 is False and t2.pk == transfer.pk


@pytest.mark.django_db
def test_wrong_code_zero_movement(challenged, oob_capture):
    sender, receiver, src, dst = challenged
    key = f"RS-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    bal_src, bal_dst = account_balance(src.ledger_account), account_balance(dst.ledger_account)
    journals = JournalEntry.objects.count()

    from apps.transfers.services import resume_transfer

    with pytest.raises(Exception) as e:
        resume_transfer(actor=sender, challenge_id=err.challenge_id,
                        code="000000", facts=dict(err.facts))
    assert "INVALID_CODE" in str(e.value)
    ch = RiskChallenge.objects.get(pk=err.challenge_id)
    assert ch.status == RiskChallenge.Status.PENDING          # still answerable
    assert account_balance(src.ledger_account) == bal_src     # ledger untouched
    assert account_balance(dst.ledger_account) == bal_dst
    assert JournalEntry.objects.count() == journals


@pytest.mark.django_db
def test_tampered_facts_kill_challenge_zero_movement(challenged, oob_capture):
    from apps.transfers.models import Transfer
    from apps.transfers.services import resume_transfer

    sender, receiver, src, dst = challenged
    key = f"RS-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    tampered = {**err.facts, "amount": "9999.00"}

    with pytest.raises(Exception) as e:
        resume_transfer(actor=sender, challenge_id=err.challenge_id,
                        code=_delivered_code(oob_capture, err.challenge_id),
                        facts=tampered)
    assert "MATERIAL_CHANGED" in str(e.value)
    ch = RiskChallenge.objects.get(pk=err.challenge_id)
    assert ch.status == RiskChallenge.Status.EXPIRED
    assert not Transfer.objects.filter(idempotency_key=key).exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_resume_single_settlement(challenged, oob_capture):
    """Two simultaneous resumes of one challenge: at most ONE settlement."""
    from apps.transfers.models import Transfer, TransferStatus
    from apps.transfers.services import resume_transfer

    sender, receiver, src, dst = challenged
    key = f"RS-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    code = _delivered_code(oob_capture, err.challenge_id)
    results = []

    def worker():
        try:
            t, created = resume_transfer(actor=sender,
                                         challenge_id=err.challenge_id,
                                         code=code, facts=dict(err.facts))
            results.append(("ok", created))
        except Exception as e:
            results.append(("err", str(e)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    completed = Transfer.objects.filter(idempotency_key=key,
                                        status=TransferStatus.COMPLETED).count()
    oks = [r for r in results if r[0] == "ok"]
    assert completed <= 1
    assert len(oks) <= 1                       # never two settlements
    assert len(results) == 2                   # both requests got an answer
    ch = RiskChallenge.objects.get(pk=err.challenge_id)
    assert ch.status == RiskChallenge.Status.CONSUMED


@pytest.mark.django_db
def test_double_submit_after_success_is_idempotent(challenged, oob_capture):
    """Second identical resume after success returns the settled transfer."""
    from apps.transfers.services import resume_transfer

    sender, receiver, src, dst = challenged
    key = f"RS-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    code = _delivered_code(oob_capture, err.challenge_id)
    payload = dict(err.facts)
    t1, c1 = resume_transfer(actor=sender, challenge_id=err.challenge_id,
                             code=code, facts=payload)
    # double submit: idempotency short-circuits BEFORE the gate — the settled
    # transfer is returned again, never a second settlement
    t2, c2 = resume_transfer(actor=sender, challenge_id=err.challenge_id,
                             code=code, facts=payload)
    assert c2 is False and t2.pk == t1.pk
    from apps.transfers.models import Transfer, TransferStatus

    assert Transfer.objects.filter(idempotency_key=key,
                                   status=TransferStatus.COMPLETED).count() == 1


# ------------------------------------------------------------------ UI flow

@pytest.mark.django_db
def test_ui_journey_transfer_panel_then_confirm(challenged, oob_capture):
    sender, receiver, src, dst = challenged
    c = Client()
    c.force_login(sender)
    key = f"RS-{uuid4().hex[:10]}"

    r = c.post("/transfers/", {
        "source_account": str(src.pk),
        "destination_account": str(dst.pk),
        "amount": "10.00",
        "description": "ui journey",
    }, headers={"Idempotency-Key": key})
    body = r.content.decode()
    assert r.status_code == 400
    assert "Step-up verification required" in body
    ch = RiskChallenge.objects.latest("pk")
    assert f"/security/challenge/{ch.pk}/" in body
    assert 'name="fact_amount"' in body and f'value="{src.pk}"' in body
    assert ch.code_hash not in body            # no secrets in the panel

    code = _delivered_code(oob_capture, ch.pk)
    r2 = c.post(f"/security/challenge/{ch.pk}/", {
        "code": code,
        "fact_source_account": str(src.pk),
        "fact_destination_account": str(dst.pk),
        "fact_beneficiary": "",
        "fact_amount": "10.00",
        "fact_idempotency_key": key,
        "description": "ui journey",
    })
    assert r2.status_code == 302 and "/transfers" in r2["Location"]
    from apps.transfers.models import Transfer, TransferStatus

    t = Transfer.objects.get(idempotency_key=key)
    assert t.status == TransferStatus.COMPLETED and t.description == "ui journey"


@pytest.mark.django_db
def test_ui_confirm_with_wrong_code_keeps_operation_unsettled(challenged, oob_capture):
    sender, receiver, src, dst = challenged
    c = Client()
    c.force_login(sender)
    key = f"RS-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    bal_src = account_balance(src.ledger_account)

    r = c.post(f"/security/challenge/{err.challenge_id}/", {
        "code": "999999",
        **{f"fact_{k}": v for k, v in err.facts.items()},
    })
    assert r.status_code == 400
    from apps.transfers.models import Transfer

    assert not Transfer.objects.filter(idempotency_key=key).exists()
    assert account_balance(src.ledger_account) == bal_src
