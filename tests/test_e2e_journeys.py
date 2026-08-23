"""Critical user journeys (Django test client E2E). Browser-level journeys verified interactively."""
from decimal import Decimal

import pytest

from apps.transfers.models import Transfer

from .conftest import make_user


def _login(client, username, password="Test!12345"):
    return client.post("/login/", {"username": username, "password": password})


def test_journey_1_login_transfer_history(aubrey, account_factory, bob, client):
    src = account_factory(aubrey, "1000.00")
    dst = account_factory(bob, "0.00")
    # login -> dashboard
    r = _login(client, "aubrey")
    assert r.status_code == 302
    assert client.get("/").status_code == 200
    # transfer
    r = client.post("/transfers/", {"source_account": src.pk, "amount": "40.00",
                                    "destination_account": dst.pk, "description": "journey"})
    assert r.status_code == 302
    t = Transfer.objects.get()
    assert t.status == "COMPLETED"
    src.refresh_from_db()
    assert src.current_balance == Decimal("960.00")
    # history shows it
    r = client.get("/transfers/")
    assert b"TRF-" in r.content


def test_journey_4_idor_access_denied(aubrey, bob, account_factory, client):
    bob_acc = account_factory(bob, "777.00")
    _login(client, "aubrey")
    r = client.post("/transfers/", {"source_account": bob_acc.pk, "amount": "10.00",
                                    "destination_account": None or ""})
    assert r.status_code == 400
    bob_acc.refresh_from_db()
    assert bob_acc.current_balance == Decimal("777.00")


def test_scheduled_job_command(alice, bob, account_factory):
    from django.core.management import call_command
    from django.utils import timezone

    from apps.transfers.services import execute_transfer

    t, _ = execute_transfer(actor=alice, source_account_id=alice.checking.pk,
                            amount="25.00", destination_account_id=bob.checking.pk,
                            scheduled_for=timezone.now() - timezone.timedelta(minutes=1))
    assert t.status == "PENDING"
    call_command("run_scheduled_jobs")
    t.refresh_from_db()
    assert t.status == "COMPLETED"
