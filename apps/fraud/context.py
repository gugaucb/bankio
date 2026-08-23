"""Standardized risk context (spec PART 3).

One context shape serves every Bankio domain. Only fields with a defensible
fraud/security use are collected (§23) — no browsing history, no free-form
profiling. The context is built server-side; clients cannot inject it.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from django.utils import timezone


@dataclass(frozen=True)
class RiskContext:
    operation_type: str
    actor: Optional[object] = None          # auth user performing the request
    customer: Optional[object] = None       # customer on whose behalf money moves
    amount: Optional[Decimal] = None
    currency: str = ""
    account_ref: str = ""                   # operational account number / pk reference
    beneficiary_id: Optional[int] = None
    device_id: str = ""                     # identity device hash (UA + accept-language)
    session_key: str = ""
    ip: str = ""
    timestamp: datetime = field(default_factory=timezone.now)
    # domain correlation: links the evaluation to the banking idempotency key
    idempotency_key: str = ""

    def __post_init__(self):
        if not self.operation_type:
            raise ValueError("RiskContext requires an operation_type.")
        if self.timestamp.tzinfo is None:
            raise ValueError("RiskContext.timestamp must be timezone-aware.")

    def evaluation_fields(self):
        """Projection onto RiskEvaluation columns."""
        return {
            "operation_type": self.operation_type,
            "actor": self.actor,
            "customer": self.customer,
            "amount": self.amount,
            "currency": self.currency,
            "resource_reference": self.account_ref or self.beneficiary_id or "",
            "idempotency_key": self.idempotency_key,
        }


def client_ip(request):
    return request.META.get("REMOTE_ADDR", "") or ""


def from_request(request, *, operation_type, amount=None, currency="", **domain_refs):
    """Build a RiskContext for an incoming HTTP request.

    Device hash mirrors apps.identity.services._device_hash so signals can
    correlate with authentication history. Domain callers may attach
    account/beneficiary/idempotency references via kwargs.
    """
    import hashlib

    ua = request.META.get("HTTP_USER_AGENT", "")
    lang = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    device_id = hashlib.sha256(f"{ua}|{lang}".encode()).hexdigest()[:64]

    user = getattr(request, "user", None)
    actor = user if getattr(user, "is_authenticated", False) else None
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", "") or "" if session is not None else ""

    return RiskContext(
        operation_type=operation_type,
        actor=actor,
        customer=getattr(actor, "customer", None) if actor else None,
        amount=Decimal(amount) if amount is not None else None,
        currency=currency,
        device_id=device_id,
        session_key=session_key,
        ip=client_ip(request),
        **domain_refs,
    )
