"""Customer-facing step-up challenge surface (closes the challenge loop).

GET  /security/challenge/<id>/ presents the challenge (state + minimal
     operation context) — never validates anything.
POST /security/challenge/<id>/ validates the code SERVER-SIDE via the
     existing verify_challenge() service. GET can never confirm.

Security contract:
  - ownership enforced with 404 (no existence leak → IDOR-safe);
  - code_hash / material_hash / raw code / evaluation internals never
    reach the template;
  - material facts arrive as whitelisted hidden fields and are only
    trusted after SHA-256 binding verification (tamper → MATERIAL_CHANGED);
  - CSRF required (Django middleware); POST-only validation.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render

from .challenge import ChallengeError, _expire_if_due, verify_challenge
from .models import RiskChallenge

# whitelisted material facts a resuming client may carry back to us;
# anything else in the POST is ignored
FACT_KEYS = ("amount", "beneficiary", "source_account", "destination_account",
             "idempotency_key")

_STATE_MESSAGES = {
    RiskChallenge.Status.EXPIRED: "This challenge has expired. Please restart the operation.",
    RiskChallenge.Status.CONSUMED: "This challenge was already used to authorize an operation.",
    RiskChallenge.Status.VERIFIED: "This challenge was already verified.",
}

_ERROR_MESSAGES = {
    "CHALLENGE_EXPIRED": _STATE_MESSAGES[RiskChallenge.Status.EXPIRED],
    "CHALLENGE_NOT_PENDING": _STATE_MESSAGES[RiskChallenge.Status.CONSUMED],
    "MATERIAL_CHANGED": ("Operation details changed since this code was issued. "
                         "The challenge was invalidated for your protection."),
    "INVALID_CODE": "Invalid code. Please try again.",
    "MISSING_CONTEXT": "Missing operation context — restart the operation and try again.",
}


def _own_challenge(request, challenge_id):
    """Owner-or-404: another user's challenge is indistinguishable from a
    nonexistent one (IDOR hardening)."""
    challenge = (
        RiskChallenge.objects.select_related("evaluation")
        .filter(pk=challenge_id).first()
    )
    if challenge is None:
        raise Http404("No challenge here.")
    user = request.user
    if not (user.is_superuser or challenge.customer_id == user.id):
        raise Http404("No challenge here.")
    return challenge


def _posted_facts(request):
    return {
        key[len("fact_"):]: value
        for key, value in request.POST.items()
        if key.startswith("fact_") and key[len("fact_"):] in FACT_KEYS
    }


@login_required
def challenge_detail(request, challenge_id):
    challenge = _own_challenge(request, challenge_id)
    _expire_if_due(challenge)   # lazy TTL: a stale PENDING shows as expired
    evaluation = challenge.evaluation

    error = None
    if request.method == "POST":
        facts = _posted_facts(request)
        if not facts:
            error = "MISSING_CONTEXT"
        else:
            try:
                verify_challenge(challenge, request.POST.get("code") or "", facts)
            except ChallengeError as exc:
                error = str(exc)
                # re-read: verify may have transitioned the challenge (e.g.
                # material change → EXPIRED) and the banner must explain why
                challenge.refresh_from_db()
            else:
                messages.success(request, "Verification code confirmed.")
                return redirect("fraud:stepup_challenge", challenge_id=challenge.pk)

    context = {
        "nav": "security",
        "page_heading": "Confirm your operation",
        "challenge": challenge,
        # minimal, non-secret operation context from the stored evaluation
        "operation_type": evaluation.operation_type,
        "amount": evaluation.amount,
        "currency": evaluation.currency,
        "state_message": (
            _ERROR_MESSAGES.get(error, "Something went wrong.")
            if error else _STATE_MESSAGES.get(challenge.status)
        ),
    }
    response = render(request, "security/challenge.html", context)
    if error:
        response.status_code = 400
    return response
