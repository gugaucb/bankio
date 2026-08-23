"""Signal registry (spec PART 4).

Signals are FACTS. Rules interpret facts — no rule logic lives here.
Each signal is a pure, deterministic function of the risk context plus
explicitly supplied domain objects. Signals are isolated: one failing
signal yields an ERROR marker instead of corrupting the whole collection.
"""
from django.utils import timezone

REGISTRY = {}


def register(signal_id):
    def deco(fn):
        REGISTRY[signal_id] = fn
        return fn

    return deco


def collect(ctx, signal_ids=None, **domain_objects):
    """Collect signals for a context. Returns {signal_id: value} where a
    failed signal maps to {"__error__": reason} and never raises."""
    wanted = signal_ids or list(REGISTRY)
    out = {}
    for sid in wanted:
        fn = REGISTRY.get(sid)
        if fn is None:
            out[sid] = {"__error__": "unknown signal"}
            continue
        try:
            value = fn(ctx, **domain_objects)
        except Exception as exc:  # isolated on purpose; fail-safe handled at policy layer
            value = {"__error__": f"{type(exc).__name__}: {exc}"}
        out[sid] = value
    return out


# --- operation signals -------------------------------------------------------

@register("TRANSACTION_AMOUNT")
def transaction_amount(ctx):
    return str(ctx.amount) if ctx.amount is not None else None


@register("TIME_OF_DAY_HOUR")
def time_of_day(ctx):
    return ctx.timestamp.hour


@register("ACCOUNT_AGE_DAYS")
def account_age_days(ctx, account=None):
    if account is None:
        return None
    return (ctx.timestamp - account.created_at).days


@register("CUSTOMER_TENURE_DAYS")
def customer_tenure_days(ctx, user=None):
    if user is None:
        return None
    return (ctx.timestamp - user.date_joined).days


# --- device / authentication history signals ---------------------------------

def _known_device(user, device_id):
    if user is None or not device_id:
        return None
    from apps.identity.models import Device

    return Device.objects.filter(user=user, device_id=device_id).first()


@register("NEW_DEVICE")
def new_device(ctx, user=None):
    dev = _known_device(user or ctx.actor, ctx.device_id)
    if dev is None:
        return True  # unknown/untracked device counts as new
    return not dev.trusted


@register("DEVICE_FIRST_SEEN_HOURS")
def device_first_seen_hours(ctx, user=None):
    dev = _known_device(user or ctx.actor, ctx.device_id)
    if dev is None:
        return None
    return round((ctx.timestamp - dev.first_seen).total_seconds() / 3600, 2)


def _recent_audit(user_or_none, action, hours=24):
    from apps.audit.models import AuditLog

    qs = AuditLog.objects.filter(action=action, timestamp__gte=timezone.now() - timezone.timedelta(hours=hours))
    if user_or_none is not None:
        qs = qs.filter(actor=user_or_none)
    return qs


@register("FAILED_LOGIN_COUNT_24H")
def failed_login_count(ctx, user=None):
    return _recent_audit(user or ctx.actor, "LOGIN_FAILED").count()


@register("PASSWORD_CHANGED_RECENTLY_24H")
def password_changed_recently(ctx, user=None):
    return _recent_audit(user or ctx.actor, "PASSWORD_CHANGED").exists()
