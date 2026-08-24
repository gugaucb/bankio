"""Branch 4 — hardening: brute-force limit, cooldown reissue, concurrency."""
import logging
import threading
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.audit.models import AuditLog
from apps.fraud import modes
from apps.fraud.challenge import ChallengeError
from apps.fraud.challenge_guard import MAX_ATTEMPTS, confirm, reissue_challenge
from apps.fraud.models import FraudEngineSetting, RiskChallenge, RiskEvaluation, RiskRule
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
    sender, receiver, src, dst = accounts_setup
    RiskRule.objects.create(rule_id="HRD-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    mgr = make_user("hrd-mgr", role="FRAUD_MANAGER")
    modes.set_mode(RiskEvaluation.EngineMode.CHALLENGE_ONLY, actor=mgr)
    return sender, receiver, src, dst


def _start_transfer(sender, src, dst, key):
    from apps.transfers.services import TransferError, execute_transfer

    try:
        return execute_transfer(actor=sender, source_account_id=src.pk,
                                amount=Decimal("10.00"),
                                destination_account_id=dst.pk,
                                idempotency_key=key, description="")
    except TransferError as e:
        assert e.code == "STEP_UP_REQUIRED"
        return e


# ------------------------------------------------------------- brute force

@pytest.mark.django_db
def test_max_attempts_tombstones_and_correct_code_dies_too(challenged, oob_capture):
    """MAX_ATTEMPTS wrong codes kill the challenge — even knowledge of the
    correct code afterwards cannot resurrect it."""
    sender, receiver, src, dst = challenged
    err = _start_transfer(sender, src, dst, f"HB-{uuid4().hex[:10]}")
    facts = dict(err.facts)
    real_code = _delivered_code(oob_capture, err.challenge_id)

    for i in range(MAX_ATTEMPTS):
        with pytest.raises(ChallengeError) as e:
            confirm(err.challenge_id, sender, "000000", facts, "op-ref")
        assert str(e.value) == "INVALID_CODE"   # tombstone applies NEXT attempt

    with pytest.raises(ChallengeError) as e:
        confirm(err.challenge_id, sender, real_code, facts, "op-ref")
    assert str(e.value) == "CHALLENGE_EXPIRED"
    assert RiskChallenge.objects.get(pk=err.challenge_id).status == RiskChallenge.Status.EXPIRED
    expired = AuditLog.objects.filter(action="CHALLENGE_EXPIRED")
    assert any(m.get("reason") == "MAX_ATTEMPTS" for m in
               expired.values_list("metadata", flat=True))


@pytest.mark.django_db
def test_failure_audits_leak_no_secrets(challenged, oob_capture):
    sender, receiver, src, dst = challenged
    err = _start_transfer(sender, src, dst, f"HB-{uuid4().hex[:10]}")
    real_code = _delivered_code(oob_capture, err.challenge_id)
    ch = RiskChallenge.objects.get(pk=err.challenge_id)

    with pytest.raises(ChallengeError):
        confirm(err.challenge_id, sender, "000000", dict(err.facts), "op-ref")

    logs = AuditLog.objects.filter(action__in=("CHALLENGE_FAILED", "CHALLENGE_ISSUED"))
    blob = repr(list(logs.values()))
    assert real_code not in blob
    assert ch.code_hash not in blob
    assert ch.material_hash not in blob


@pytest.mark.django_db
def test_below_threshold_correct_code_still_settles_once(challenged, oob_capture):
    sender, receiver, src, dst = challenged
    key = f"HB-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    bal_src = account_balance(src.ledger_account)

    for _ in range(MAX_ATTEMPTS - 1):
        with pytest.raises(ChallengeError):
            confirm(err.challenge_id, sender, "111111", dict(err.facts), f"T:{key}")
    assert RiskChallenge.objects.get(pk=err.challenge_id).status == RiskChallenge.Status.PENDING

    from apps.transfers.models import Transfer, TransferStatus
    from apps.transfers.services import resume_transfer

    transfer, created = resume_transfer(actor=sender, challenge_id=err.challenge_id,
                                        code=_delivered_code(oob_capture, err.challenge_id),
                                        facts=dict(err.facts))
    assert created is True and transfer.status == TransferStatus.COMPLETED
    assert account_balance(src.ledger_account) == bal_src - Decimal("10.00")
    assert Transfer.objects.filter(idempotency_key=key,
                                   status=TransferStatus.COMPLETED).count() == 1


# ---------------------------------------------------------------- reissue

@pytest.mark.django_db
def test_reissue_new_challenge_old_code_dead_new_code_settles(challenged, oob_capture):
    from apps.transfers.models import Transfer, TransferStatus
    from apps.transfers.services import resume_transfer

    sender, receiver, src, dst = challenged
    key = f"HB-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    old = RiskChallenge.objects.get(pk=err.challenge_id)
    old_code = _delivered_code(oob_capture, old.pk)

    new, new_code = reissue_challenge(old.pk, sender)
    assert new.pk != old.pk
    assert new.evaluation_id == old.evaluation_id
    assert new.material_hash == old.material_hash
    assert new.code_hash != old.code_hash
    assert new.status == RiskChallenge.Status.PENDING
    old.refresh_from_db()
    assert old.status == RiskChallenge.Status.EXPIRED
    # old code no longer matches the new binding
    with pytest.raises(ChallengeError) as e:
        confirm(new.pk, sender, old_code, dict(err.facts), f"T:{key}")
    assert str(e.value) == "INVALID_CODE"

    transfer, created = resume_transfer(actor=sender, challenge_id=new.pk,
                                        code=new_code, facts=dict(err.facts))
    assert created is True and transfer.status == TransferStatus.COMPLETED
    assert not Transfer.objects.exclude(pk=transfer.pk).filter(idempotency_key=key).exists()
    reissued = AuditLog.objects.filter(action="CHALLENGE_REISSUED")
    assert reissued.count() == 1


@pytest.mark.django_db(transaction=True)
def test_reissue_cooldown_blocks_spam(challenged, oob_capture):
    sender, receiver, src, dst = challenged
    err = _start_transfer(sender, src, dst, f"HB-{uuid4().hex[:10]}")
    reissue_challenge(err.challenge_id, sender)

    err2 = _start_transfer(sender, src, dst, f"HB-{uuid4().hex[:10]}")
    with pytest.raises(ChallengeError) as e:
        reissue_challenge(err2.challenge_id, sender)
    assert str(e.value) == "REISSUE_COOLDOWN"


@pytest.mark.django_db
def test_reissue_only_for_pending(challenged, oob_capture):
    sender, receiver, src, dst = challenged
    key = f"HB-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    confirm(err.challenge_id, sender, _delivered_code(oob_capture, err.challenge_id),
            dict(err.facts), f"T:{key}")
    with pytest.raises(ChallengeError) as e:
        reissue_challenge(err.challenge_id, sender)
    assert str(e.value) == "CHALLENGE_NOT_PENDING"


@pytest.mark.django_db
def test_reissue_foreign_or_missing_denied(accounts_setup, db, oob_capture):
    sender, receiver, src, dst = accounts_setup
    stranger = make_user("hrd-stranger")
    RiskRule.objects.create(rule_id="HRD2-BLOCK", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True)
    mgr = make_user("hrd2-mgr", role="FRAUD_MANAGER")
    modes.set_mode(RiskEvaluation.EngineMode.CHALLENGE_ONLY, actor=mgr)

    err = _start_transfer(sender, src, dst, f"HB-{uuid4().hex[:10]}")
    with pytest.raises(ChallengeError) as e:
        reissue_challenge(err.challenge_id, stranger)
    assert str(e.value) == "CHALLENGE_NOT_FOUND"


@pytest.mark.django_db
def test_material_tamper_tombstone_survives_rollback(challenged, oob_capture):
    sender, receiver, src, dst = challenged
    err = _start_transfer(sender, src, dst, f"HB-{uuid4().hex[:10]}")
    tampered = {**err.facts, "amount": "9999.00"}
    with pytest.raises(ChallengeError) as e:
        confirm(err.challenge_id, sender, "000000", tampered, "op")
    assert str(e.value) == "MATERIAL_CHANGED"
    assert RiskChallenge.objects.get(pk=err.challenge_id).status == RiskChallenge.Status.EXPIRED


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirm_single_consumption(challenged, oob_capture):
    """Two simultaneous confirms of one challenge → ≤1 success."""
    sender, receiver, src, dst = challenged
    key = f"HB-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    code = _delivered_code(oob_capture, err.challenge_id)
    results = []

    def worker():
        try:
            confirm(err.challenge_id, sender, code, dict(err.facts), f"T:{key}")
            results.append("ok")
        except ChallengeError as e:
            results.append(str(e))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") <= 1
    assert len(results) == 2
    assert RiskChallenge.objects.get(pk=err.challenge_id).status == RiskChallenge.Status.CONSUMED


# ------------------------------------------------------- UI counts attempts

@pytest.mark.django_db
def test_ui_wrong_codes_count_toward_brute_force_limit(challenged, oob_capture):
    """The standalone page POST path feeds the same brute-force counter."""
    from django.test import Client

    sender, receiver, src, dst = challenged
    c = Client()
    c.force_login(sender)
    key = f"HB-{uuid4().hex[:10]}"
    err = _start_transfer(sender, src, dst, key)
    payload = {"code": "000000", **{f"fact_{k}": v for k, v in err.facts.items()}}

    for _ in range(MAX_ATTEMPTS - 1):
        r = c.post(f"/security/challenge/{err.challenge_id}/", payload)
        assert r.status_code == 400
    # last allowed wrong attempt via UI also tombstones on the next check;
    # the counter is shared with the service layer
    assert RiskChallenge.objects.get(pk=err.challenge_id).status == RiskChallenge.Status.PENDING
    r = c.post(f"/security/challenge/{err.challenge_id}/", payload)
    assert r.status_code == 400
    ch = RiskChallenge.objects.get(pk=err.challenge_id)
    ch.refresh_from_db()
    assert ch.status == RiskChallenge.Status.EXPIRED
