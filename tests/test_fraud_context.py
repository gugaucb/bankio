"""Risk context: server-built, minimal, domain-agnostic."""
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from django.test import RequestFactory

from apps.fraud.context import RiskContext, from_request


def test_requires_operation_type_and_tz_aware_timestamp():
    from datetime import datetime

    with pytest.raises(ValueError):
        RiskContext(operation_type="")
    naive = datetime(2026, 1, 1)
    with pytest.raises(ValueError):
        RiskContext(operation_type="TRANSFER", timestamp=naive)


def test_context_is_immutable():
    ctx = RiskContext(operation_type="TRANSFER")
    with pytest.raises(FrozenInstanceError):
        ctx.amount = Decimal("1")


def test_from_request_extracts_server_side_facts():
    rf = RequestFactory()
    req = rf.post(
        "/transfers",
        HTTP_USER_AGENT="test-agent",
        HTTP_ACCEPT_LANGUAGE="pt-BR",
        REMOTE_ADDR="10.0.0.9",
    )
    ctx = from_request(req, operation_type="TRANSFER", amount=Decimal("100"))
    assert ctx.operation_type == "TRANSFER"
    assert ctx.ip == "10.0.0.9"
    assert len(ctx.device_id) == 64
    assert ctx.actor is None  # anonymous request -> no actor


def test_same_device_headers_same_hash():
    rf = RequestFactory()

    def mk():
        return from_request(
            rf.post("/x", HTTP_USER_AGENT="a", HTTP_ACCEPT_LANGUAGE="en"),
            operation_type="LOGIN",
        )

    assert mk().device_id == mk().device_id


def test_evaluation_fields_projection_matches_model_columns():
    ctx = RiskContext(
        operation_type="CARD_PURCHASE",
        amount=Decimal("55.00"),
        currency="USD",
        idempotency_key="op-1",
    )
    fields = ctx.evaluation_fields()
    assert fields["operation_type"] == "CARD_PURCHASE"
    assert fields["currency"] == "USD"
    assert fields["idempotency_key"] == "op-1"
