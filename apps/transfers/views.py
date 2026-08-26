from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Account, Beneficiary
from apps.audit.services import record as audit
from .models import Transfer
from .services import TransferError, execute_transfer


@login_required
def transfer_list_create(request):
    accounts = Account.objects.filter(customer=request.user)
    beneficiaries = Beneficiary.objects.filter(owner=request.user)
    transfers = Transfer.objects.filter(
        source_account__customer=request.user
    ).order_by("-created_at")

    if request.method == "POST":
        form = request.POST
        dest_raw = (form.get("destination_account") or "").strip()
        dest_id = None
        if dest_raw:
            # account numbers are all-numeric, so try account_number first
            # and fall back to pk for internal shortcuts
            acct = Account.objects.filter(account_number=dest_raw).first()
            if acct is None and dest_raw.isdigit():
                acct = Account.objects.filter(pk=dest_raw).first()
            dest_id = acct.pk if acct else None
        try:
            transfer, created = execute_transfer(
                actor=request.user,
                source_account_id=form.get("source_account"),
                amount=form.get("amount"),
                destination_account_id=dest_id,
                beneficiary_id=form.get("beneficiary") or None,
                description=form.get("description", ""),
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
            if request.headers.get("HX-Request"):
                return render(request, "transfers/_result.html", {"transfer": transfer, "created": created})
            return redirect("transfers")
        except (TransferError, ValueError, Account.DoesNotExist) as e:
            step_up = None
            facts = getattr(e, "facts", None)
            if getattr(e, "code", "") == "STEP_UP_REQUIRED" and facts:
                # panel carries the CANONICAL material facts used at issuance;
                # they are re-validated against the material hash on confirm
                step_up = {"challenge_id": e.challenge_id, **facts}
            ctx = {"accounts": accounts, "beneficiaries": beneficiaries,
                   "transfers": transfers, "error": str(e), "step_up": step_up}
            if request.headers.get("HX-Request"):
                return render(request, "transfers/_result.html",
                              {**ctx, "created": False}, status=400)
            return render(request, "transfers/index.html", ctx, status=400)

    return render(request, "transfers/index.html", {
        "accounts": accounts, "beneficiaries": beneficiaries, "transfers": transfers,
    })


@login_required
def quick_transfer_form(request):
    """HTMX fragment: prefilled transfer form for a recent recipient."""
    account_number = request.GET.get("account_number", "")
    return render(request, "dashboard/_quick_transfer_form.html", {
        "accounts": Account.objects.filter(customer=request.user),
        "account_number": account_number,
    })
