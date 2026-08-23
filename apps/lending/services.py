"""Lending: simulation, credit analysis, approval, disbursement, repayment."""
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from apps.accounts.models import AccountStatus
from apps.ledger import services as ledger

from .models import LoanApplication, RepaymentSchedule


def monthly_payment(amount, annual_rate_pct, months: int) -> Decimal:
    """Standard amortization (PMT)."""
    if months <= 0:
        raise ValueError("months must be positive")
    amount = Decimal(str(amount))
    annual_rate_pct = Decimal(str(annual_rate_pct))
    r = (annual_rate_pct / Decimal(100)) / Decimal(12)
    if r == 0:
        return (amount / months).quantize(Decimal("0.01"), ROUND_HALF_UP)
    factor = (1 + r) ** months
    pmt = amount * r * factor / (factor - 1)
    return pmt.quantize(Decimal("0.01"), ROUND_HALF_UP)


def simulate(amount, annual_rate_pct, months):
    pmt = monthly_payment(Decimal(str(amount)), Decimal(str(annual_rate_pct)), months)
    total = (pmt * months).quantize(Decimal("0.01"))
    return {"monthly": pmt, "total": total, "interest": (total - Decimal(str(amount))).quantize(Decimal("0.01"))}


def credit_score(application) -> int:
    """Simple deterministic sandbox scoring."""
    base = 600
    if application.customer.accounts.filter(status="ACTIVE").exists():
        base += 100
    ratio = application.amount / max(Decimal(str(application.product.max_amount)), Decimal("1"))
    base -= int(ratio * 100)
    return max(300, min(850, base))


@transaction.atomic
def apply_for_loan(*, customer, product, amount, term_months, disbursed_account):
    amount = Decimal(str(amount))
    if amount < Decimal(str(product.min_amount)) or amount > Decimal(str(product.max_amount)):
        raise ValueError("Amount outside product range")
    app = LoanApplication.objects.create(
        customer=customer, product=product, amount=amount,
        term_months=term_months, interest_rate=product.base_rate,
        status="SUBMITTED", disbursed_account=disbursed_account,
    )
    app.score = credit_score(app)
    app.status = "REVIEW"
    app.save(update_fields=["score", "status"])
    return app


@transaction.atomic
def approve(application, manager):
    if application.status != "REVIEW":
        raise ValueError("Not in review")
    if application.score is None or application.score < 550:
        application.status = "REJECTED"
        application.reviewed_by = manager
        application.save(update_fields=["status", "reviewed_by"])
        return application
    application.status = "APPROVED"
    application.reviewed_by = manager
    application.save(update_fields=["status", "reviewed_by"])
    result = disburse(application)
    application.status = result.status  # reflect disbursement outcome on caller's instance
    return application


@transaction.atomic
def disburse(application):
    locked = LoanApplication.objects.select_for_update().get(pk=application.pk)
    existing = ledger.find_idempotent(f"loan-disburse:{locked.pk}")
    if existing:
        refreshed = LoanApplication.objects.get(pk=existing.result["application_id"])
        application.status = refreshed.status
        return application
    application = locked  # operate on the row-locked instance
    account = application.disbursed_account
    if account.status != AccountStatus.ACTIVE:
        raise ValueError("Disbursement account not active")
    bank_loan_asset = ledger.get_or_create_account("3100-LOAN-PORTFOLIO", "Loan Portfolio", type="ASSET")
    journal = ledger.post_journal(
        reference=f"LOAN-{uuid.uuid4().hex[:10].upper()}",
        description=f"Loan disbursement {application.pk}",
        lines=[
            (bank_loan_asset, "DEBIT", application.amount),
            (account.ledger_account, "CREDIT", application.amount),
        ],
    )
    application.status = "ACTIVE"
    application.save(update_fields=["status"])

    pmt = monthly_payment(application.amount, application.interest_rate, application.term_months)
    from datetime import date

    for i in range(1, application.term_months + 1):
        RepaymentSchedule.objects.create(
            application=application, installment_no=i,
            due_date=add_months(date.today(), i), amount=pmt,
        )
    ledger.record_idempotent(
        f"loan-disburse:{application.pk}", "LOAN_DISBURSEMENT", journal,
        {"application_id": application.pk},
    )
    return application


@transaction.atomic
def repay_installment(schedule, actor, idempotency_key=None):
    original = schedule
    schedule = RepaymentSchedule.objects.select_for_update().get(pk=schedule.pk)
    key = f"loan-repay:{idempotency_key}" if idempotency_key else f"loan-repay:{schedule.pk}"
    existing = ledger.find_idempotent(key)
    if existing:
        refreshed = RepaymentSchedule.objects.get(pk=existing.result["schedule_id"])
        original.paid_at = refreshed.paid_at
        original.journal_id = refreshed.journal_id
        return original
    if schedule.paid_at:
        raise ValueError("Installment already paid")
    account = schedule.application.disbursed_account
    if account.available_balance < schedule.amount:
        raise ValueError("Insufficient funds")
    portfolio = ledger.get_or_create_account("3100-LOAN-PORTFOLIO", "Loan Portfolio", type="ASSET")
    journal = ledger.post_journal(
        reference=f"LRP-{uuid.uuid4().hex[:10].upper()}",
        description=f"Loan installment {schedule.application_id}/{schedule.installment_no}",
        lines=[
            (account.ledger_account, "DEBIT", schedule.amount),
            (portfolio, "CREDIT", schedule.amount),
        ],
    )
    from django.utils import timezone as tz

    schedule.paid_at = tz.now()
    schedule.journal = journal
    schedule.save(update_fields=["paid_at", "journal"])
    if not schedule.application.schedule.filter(paid_at__isnull=True).exists():
        schedule.application.status = "PAID"
        schedule.application.save(update_fields=["status"])
    ledger.record_idempotent(key, "LOAN_REPAYMENT", journal, {"schedule_id": schedule.pk})
    # reflect state on the caller's instance (it was re-fetched under lock)
    original.paid_at = schedule.paid_at
    original.journal_id = schedule.journal_id
    return original


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    from calendar import monthrange
    return d.replace(year=d.year + y, month=m + 1, day=min(d.day, monthrange(d.year + y, m + 1)[1]))
