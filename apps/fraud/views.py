"""Fraud Operations console (spec PART 13).

Internal-only views, guarded by fraud RBAC — never reachable by customers.
Views authorize and render; decisions/behavior live in services.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.audit.services import record as audit
from apps.identity.models import Role

from . import cases as case_services
from . import modes
from .models import FraudAlert, FraudCase, RiskEvaluation
from .rbac import has_permission


def _require_fraud_user(request):
    if not request.user.is_authenticated:
        raise PermissionDenied("login required")
    allowed = (
        Role.FRAUD_ANALYST,
        Role.SENIOR_FRAUD_ANALYST,
        Role.FRAUD_MANAGER,
    )
    if not (request.user.is_superuser or request.user.role in allowed):
        raise PermissionDenied("fraud role required")


@login_required
def dashboard(request):
    _require_fraud_user(request)
    evaluations = RiskEvaluation.objects.exclude(engine_mode="DISABLED")
    decision_counts = {
        item["decision"]: item["n"]
        for item in evaluations.values("decision").annotate(n=Count("id"))
    }
    rule_counts = {}
    for ev in evaluations.exclude(triggered_rules=[])[:500]:
        for rule in ev.triggered_rules:
            key = f"{rule['rule_id']} v{rule['version']}"
            rule_counts[key] = rule_counts.get(key, 0) + 1
    top_rules = sorted(rule_counts.items(), key=lambda kv: -kv[1])[:5]
    ctx = {
        "open_alerts": FraudAlert.objects.filter(status=FraudAlert.Status.OPEN).count(),
        "critical_alerts": FraudAlert.objects.filter(status=FraudAlert.Status.OPEN, severity="HIGH").count(),
        "cases_investigating": FraudCase.objects.filter(
            status__in=[FraudCase.Status.OPEN, FraudCase.Status.INVESTIGATING]
        ).count(),
        "blocked": decision_counts.get(RiskEvaluation.Decision.BLOCK, 0),
        "challenges": decision_counts.get(RiskEvaluation.Decision.CHALLENGE, 0),
        "reviewed": decision_counts.get(RiskEvaluation.Decision.REVIEW, 0),
        "total_evaluations": evaluations.count(),
        "risk_distribution": {
            level: n
            for level, n in evaluations.values("risk_level").annotate(n=Count("id")).values_list("risk_level", "n")
        },
        "top_rules": top_rules,
        "engine_mode": modes.get_mode(),
        "can_manage_policies": has_permission(request.user, "manage_policies"),
    }
    return render(request, "fraud/dashboard.html", ctx)


@login_required
def alert_queue(request):
    _require_fraud_user(request)
    alerts = FraudAlert.objects.select_related("customer").order_by("-created_at")
    status = request.GET.get("status")
    severity = request.GET.get("severity")
    if status:
        alerts = alerts.filter(status=status)
    if severity:
        alerts = alerts.filter(severity=severity)
    return render(request, "fraud/alerts.html", {
        "alerts": alerts[:100], "status": status or "", "severity": severity or "",
    })


@login_required
def case_view(request, case_id):
    _require_fraud_user(request)
    case = get_object_or_404(FraudCase.objects.prefetch_related("alerts", "events"), pk=case_id)
    evaluation = None
    first_alert = case.alerts.first()
    if first_alert and first_alert.evaluation_id:
        evaluation = first_alert.evaluation
    return render(request, "fraud/case.html", {
        "case": case,
        "events": case.events.select_related("actor"),
        "evaluation": evaluation,
        "can_confirm": has_permission(request.user, "confirm_fraud"),
    })


@login_required
def acknowledge_alert(request, alert_id):
    _require_fraud_user(request)
    if request.method != "POST":
        raise PermissionDenied("POST required")
    alert = get_object_or_404(FraudAlert, pk=alert_id)
    if alert.status == FraudAlert.Status.OPEN:
        alert.status = FraudAlert.Status.ACKNOWLEDGED
        alert.save(update_fields=["status"])
        audit(actor=request.user, action="FRAUD_ALERT_ACKNOWLEDGED", resource=alert)
        messages.success(request, "Alert acknowledged.")
    return redirect("fraud:alert_queue")


@login_required
def open_case_from_alert(request, alert_id):
    _require_fraud_user(request)
    if request.method != "POST":
        raise PermissionDenied("POST required")
    if not has_permission(request.user, "claim_case"):
        raise PermissionDenied("claim_case required")
    alert = get_object_or_404(FraudAlert, pk=alert_id)
    case = case_services.open_case(alert.customer, [alert], severity=alert.severity, actor=request.user)
    audit(actor=request.user, action="FRAUD_CASE_OPENED", resource=case)
    return redirect("fraud:case_view", case.pk)


@login_required
def decide_case(request, case_id):
    """Analyst state transitions; confirm/close need senior+ and a reason."""
    _require_fraud_user(request)
    if request.method != "POST":
        raise PermissionDenied("POST required")
    case = get_object_or_404(FraudCase, pk=case_id)
    new_status = request.POST.get("status", "")
    reason = request.POST.get("reason", "")
    perm_map = {
        FraudCase.Status.CONFIRMED_FRAUD: "confirm_fraud",
        FraudCase.Status.FALSE_POSITIVE: "close_case",
        FraudCase.Status.CLOSED: "close_case",
    }
    permission = perm_map.get(new_status)
    if permission and not has_permission(request.user, permission):
        raise PermissionDenied(f"{permission} required")
    try:
        case_services.transition(case, new_status, actor=request.user, decision_reason=reason)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("fraud:case_view", case.pk)
    audit(actor=request.user, action=f"FRAUD_CASE_{new_status}", resource=case,
          metadata={"reason_present": bool(reason)})
    return redirect("fraud:case_view", case.pk)
