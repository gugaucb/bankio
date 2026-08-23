"""Admin user-management domain services (Painel Administrativo de Usuários).

Rules enforced here, never in views/templates:
  - only ADMIN (or superuser) may call these services
  - password stored hashed only (create_user via manager + set_password)
  - no mass assignment: fields are explicit keyword arguments
  - block/unblock require a reason; actions are reversible
  - admin cannot block self; last active admin cannot be blocked
  - blocking kills DB-backed sessions of the target (no financial side effects:
    ledger/accounts/cards/fraud state are untouched — this is a SYSTEM-user block)
  - every action is audited with safe metadata (never secrets)
"""
from functools import wraps

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record as audit

from .models import Role, User


class AdminUserError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


# ------------------------------------------------------------- authorization

def _require_admin(actor):
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("authentication required")
    if not (actor.is_superuser or actor.has_role(Role.ADMIN)):
        raise PermissionDenied("ADMIN role required")


def require_admin(view_fn):
    """Decorator for admin views; server-side only."""
    @wraps(view_fn)
    def wrapper(request, *args, **kwargs):
        _require_admin(request.user)
        return view_fn(request, *args, **kwargs)

    return wrapper


# ------------------------------------------------------------------ helpers

def _kill_sessions(user):
    """Invalidate DB-backed sessions of the given user."""
    from importlib import import_module

    from django.conf import settings

    engine = import_module(settings.SESSION_ENGINE)
    for session in engine.SessionStore.get_model_class().objects.all():
        if session.get_decoded().get("_auth_user_id") == str(user.pk):
            session.delete()


def _active_admins():
    return User.objects.filter(role=Role.ADMIN, is_active=True)


def _audit_admin_action(actor, action, target, request=None, **metadata):
    audit(actor=actor, action=action, request=request, resource=target,
          metadata=metadata)


# ----------------------------------------------------------------- services

@transaction.atomic
def create_user(*, actor, username, email, password, role=Role.CUSTOMER,
                first_name="", last_name="", phone="", request=None):
    """Create a system user. Role comes from the explicit allow-list below —
    never straight from POST (no mass assignment)."""
    _require_admin(actor)
    if role not in Role.values:
        raise AdminUserError("INVALID_ROLE")
    if User.objects.filter(username=username).exists() or \
       User.objects.filter(email=email).exists():
        raise AdminUserError("DUPLICATE", "username or email already exists")
    try:
        validate_password(password)
    except DjangoValidationError as e:
        raise AdminUserError("WEAK_PASSWORD", "; ".join(e.messages))

    user = User.objects.create_user(
        username=username, email=email, password=password, role=role,
        first_name=first_name, last_name=last_name, phone=phone,
    )
    _audit_admin_action(actor, "ADMIN_USER_CREATED", user, request=request,
                        role=user.role)
    return user


@transaction.atomic
def block_user(*, actor, user_id, reason, request=None):
    """Block a system user (is_active=False) with mandatory reason.
    Reversible; zero financial side effects; sessions invalidated."""
    _require_admin(actor)
    if not (reason or "").strip():
        raise AdminUserError("REASON_REQUIRED")
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        raise AdminUserError("USER_NOT_FOUND")
    if user.pk == actor.pk:
        raise AdminUserError("SELF_BLOCK")
    if not user.is_active:
        raise AdminUserError("ALREADY_BLOCKED")
    if user.has_role(Role.ADMIN) and _active_admins().count() <= 1:
        # cannot remove the last active admin from the system
        raise AdminUserError("LAST_ADMIN")

    user.is_active = False
    user.save(update_fields=["is_active"])
    _kill_sessions(user)
    _audit_admin_action(actor, "ADMIN_USER_BLOCKED", user, request=request,
                        reason=reason.strip()[:500])
    return user


@transaction.atomic
def unblock_user(*, actor, user_id, reason="", request=None):
    """Unblock a previously blocked system user. Reversible."""
    _require_admin(actor)
    if not (reason or "").strip():
        raise AdminUserError("REASON_REQUIRED")
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        raise AdminUserError("USER_NOT_FOUND")
    if user.is_active:
        raise AdminUserError("ALREADY_ACTIVE")

    user.is_active = True
    user.save(update_fields=["is_active"])
    _audit_admin_action(actor, "ADMIN_USER_UNBLOCKED", user, request=request,
                        reason=reason.strip()[:500])
    return user


def get_user(user_id):
    return User.objects.filter(pk=user_id).first()


def list_users(*, query="", role="", status="ALL", page=1, page_size=20):
    """Server-side filtered/paginated listing. status: ALL|ACTIVE|BLOCKED."""
    qs = User.objects.all().order_by("-date_joined")
    if query:
        from django.db.models import Q

        qs = qs.filter(
            Q(username__icontains=query) | Q(email__icontains=query)
            | Q(first_name__icontains=query) | Q(last_name__icontains=query)
        )
    if role:
        qs = qs.filter(role=role)
    if status == "ACTIVE":
        qs = qs.filter(is_active=True)
    elif status == "BLOCKED":
        qs = qs.filter(is_active=False)

    total = qs.count()
    pages = max(1, -(-total // page_size))
    page = max(1, min(page, pages))
    items = qs[(page - 1) * page_size: page * page_size]
    return {"items": items, "total": total, "page": page, "pages": pages}


def recent_admin_actions(limit=10):
    """Feed the dashboard 'recent administrative actions' section."""
    from apps.audit.models import AuditLog

    return AuditLog.objects.filter(
        action__in=["ADMIN_USER_CREATED", "ADMIN_USER_BLOCKED", "ADMIN_USER_UNBLOCKED"],
    ).order_by("-timestamp")[:limit]


def admin_dashboard_stats(now=None):
    now = now or timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "total_users": User.objects.count(),
        "active_users": User.objects.filter(is_active=True).count(),
        "blocked_users": User.objects.filter(is_active=False).count(),
        "new_users_month": User.objects.filter(date_joined__gte=month_start).count(),
    }
