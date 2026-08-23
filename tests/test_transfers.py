"""Transfer engine: happy paths, failures, limits, idempotency, state machine, concurrency."""
import threading
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied

from apps.accounts.models import Beneficiary
from apps.transfers.models import Transfer, TransferStatus
from apps.transfers.services import TransferError, execute_transfer, reverse_transfer

from tests.conftest import make_user


def test_successful_internal_transfer(alice, bob):
    t, created = execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                                  amount="250.00", destination_account_id=bob.checking.pk)
    assert t.status == "COMPLETED"
    assert alice.checking.current_balance == Decimal("750.00")
    assert bob.checking.current_balance == Decimal("750.00")


def test_insufficient_funds(alice, bob):
    with pytest.raises(TransferError) as e:
        execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                         amount="1001.00", destination_account_id=bob.checking.pk)
    assert e.value.code == "INSUFFICIENT_FUNDS"
    assert alice.checking.current_balance == Decimal("1000.00")


@pytest.mark.parametrize("amount", ["0", "-50", "-0.01", "abc"])
def test_invalid_amounts(alice, bob, amount):
    with pytest.raises(TransferError):
        execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                         amount=amount, destination_account_id=bob.checking.pk)


def test_blocked_account_cannot_send_or_receive(alice, bob):
    from apps.accounts.models import AccountStatus

    bob.checking.status = AccountStatus.BLOCKED
    bob.checking.save()
    with pytest.raises(TransferError):
        execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                         amount="10.00", destination_account_id=bob.checking.pk)
    alice.checking.status = AccountStatus.CLOSED
    alice.checking.save()
    with pytest.raises(TransferError):
        execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                         amount="10.00", destination_account_id=bob.checking.pk)


def test_transaction_limit_exceeded(alice, bob):
    alice.checking.tx_limit = Decimal("100.00")
    alice.checking.daily_limit = Decimal("100000.00")
    alice.checking.save()
    with pytest.raises(TransferError) as e:
        execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                         amount="150.00", destination_account_id=bob.checking.pk)
    assert e.value.code == "TX_LIMIT_EXCEEDED"


def test_daily_limit_exceeded(alice, bob):
    alice.checking.daily_limit = Decimal("600.00")
    alice.checking.save()
    execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                     amount="400.00", destination_account_id=bob.checking.pk)
    with pytest.raises(TransferError) as e:
        execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                         amount="300.00", destination_account_id=bob.checking.pk)
    assert e.value.code == "DAILY_LIMIT_EXCEEDED"


def test_unverified_beneficiary_rejected(alice):
    b = Beneficiary.objects.create(owner=alice, name="Ghost", account_number="EXT-1", verified=False)
    with pytest.raises(TransferError) as e:
        execute_transfer(actor=alice, source_account_id=alice.checking.pk, amount="10.00", beneficiary_id=b.pk)
    assert e.value.code == "BENEFICIARY_UNVERIFIED"


def test_external_beneficiary_transfer(alice):
    b = Beneficiary.objects.create(owner=alice, name="External", account_number="EXT-2", verified=True)
    t, _ = execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                            amount="100.00", beneficiary_id=b.pk)
    assert t.status == "COMPLETED"
    assert alice.checking.current_balance == Decimal("900.00")


def test_idempotency_same_key_no_duplicate(alice, bob):
    args = dict(actor=alice, source_account_id=alice.checking.pk, amount="100.00",
                destination_account_id=bob.checking.pk, idempotency_key="KEY-1")
    t1, c1 = execute_transfer(**args)
    t2, c2 = execute_transfer(**args)
    assert c1 and not c2 and t1.pk == t2.pk
    assert alice.checking.current_balance == Decimal("900.00")
    assert Transfer.objects.count() == 1


def test_state_machine_illegal_transitions(alice, bob):
    t, _ = execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                            amount="10.00", destination_account_id=bob.checking.pk)
    assert t.status == "COMPLETED"
    with pytest.raises(ValueError):
        t.transition(TransferStatus.FAILED)  # COMPLETED -> FAILED illegal
    with pytest.raises(ValueError):
        t.transition(TransferStatus.COMPLETED)


def test_reversal(alice, bob):
    t, _ = execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                            amount="200.00", destination_account_id=bob.checking.pk)
    reverse_transfer(t, actor=alice)
    assert t.status == "REVERSED"
    assert alice.checking.current_balance == Decimal("1000.00")
    assert bob.checking.current_balance == Decimal("500.00")


@pytest.mark.django_db(transaction=True)
def test_concurrent_double_spend_prevented(user_factory, account_factory):
    """$1000 balance; two concurrent $800 transfers — exactly one must succeed.
    Uses committed data + separate connections per thread (real locking)."""
    from django.db import connections

    alice = make_user("c_alice")
    bob = make_user("c_bob")
    src = account_factory(alice, "1000.00")
    dst = account_factory(bob, "0.00")

    results = []

    def do():
        try:
            from apps.transfers.services import execute_transfer as xt

            t, created = xt(actor=alice, source_account_id=src.pk, amount="800.00",
                            destination_account_id=dst.pk,
                            idempotency_key=f"K-{threading.get_ident()}")
            results.append(("OK",))
        except TransferError:
            results.append(("ERR",))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=do) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    statuses = [r[0] for r in results]
    assert statuses.count("OK") == 1 and statuses.count("ERR") == 1
    src.refresh_from_db()
    dst.refresh_from_db()
    assert src.current_balance == Decimal("200.00")
    assert dst.current_balance == Decimal("800.00")


@pytest.mark.django_db(transaction=True)
def test_concurrent_idempotent_requests_single_transfer(user_factory, account_factory):
    from django.db import connections

    alice = make_user("i_alice")
    bob = make_user("i_bob")
    src = account_factory(alice, "1000.00")
    dst = account_factory(bob, "0.00")
    results = []

    def do():
        try:
            from apps.transfers.services import execute_transfer as xt

            t, created = xt(actor=alice, source_account_id=src.pk, amount="50.00",
                            destination_account_id=dst.pk, idempotency_key="SAME-KEY")
            results.append(t.reference)
        except TransferError:
            results.append("ERR")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=do) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    src.refresh_from_db()
    dst.refresh_from_db()
    assert len(set(results)) == 1
    assert src.current_balance == Decimal("950.00")
    assert dst.current_balance == Decimal("50.00")
