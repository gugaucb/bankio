"""Security Operations console (FASE 4.4).

Read-only staff surface over the risk engine: health/metrics, engine mode
control and the evaluation browser. Access model: FRAUD_* roles (existing
fraud RBAC) plus ADMIN and AUDITOR (read/oversight); mode CHANGES remain
gated by change_fraud_mode (segregation of duties §51).
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from apps.audit.models import AuditLog
from apps.identity.models import Role

from . import auth_metrics, modes, observability
from .models import RiskEvaluation
from .rbac import has_permission

SECOPS_ROLES = (
    Role.FRAUD_ANALYST,
    Role.SENIOR_FRAUD_ANALYST,
    Role.FRAUD_MANAGER,
    Role.ADMIN,
    Role.AUDITOR,
)


def _require_secops_user(request):
    if not request.user.is_authenticated:
        raise PermissionDenied("login required")
    if not (request.user.is_superuser or request.user.role in SECOPS_ROLES):
        raise PermissionDenied("security operations role required")


@login_required
def engine_health(request):
    """Engine health: metrics, latency budget, errors — read-only."""
    _require_secops_user(request)
    ctx = {
        "engine": observability.engine_metrics(window_hours=24),
        "login": auth_metrics.login_metrics(window_hours=24),
        "recent_errors": AuditLog.objects.filter(
            action="RISK_EVALUATION_ERROR").order_by("-timestamp")[:10],
        "current_mode": modes.get_mode(),
    }
    return render(request, "fraud/secops_health.html", {
        "nav": "secops",
        **ctx,
    })
