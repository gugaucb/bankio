"""Single entry point for creating customer-facing notifications (FASE 6).

notify() is SAFE to call from financial flows: it never raises, never
touches the ledger and audits its own failures. Monetary events must call
it via transaction.on_commit() so a notification is only created after the
settlement commit.
"""
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import record as audit

from .models import Category, Notification, NotificationPreference

logger = logging.getLogger(__name__)

# Kinds that may never be suppressed by user preferences (Branch 7 hook).
MANDATORY_NOTIFICATION_KINDS = frozenset({
    "PASSWORD_CHANGED", "MFA_DISABLED", "MFA_ENABLED",
    "NEW_DEVICE", "USER_BLOCKED", "USER_UNBLOCKED",
    "CHALLENGE_ISSUED", "CHALLENGE_REISSUED",
})

MAX_METADATA_BYTES = 2000


class NotificationError(Exception):
    pass


def _sanitize_metadata(metadata):
    """Minimization: shallow str/bool/int payload only; no secrets by design."""
    if not isinstance(metadata, dict):
        raise NotificationError("metadata must be a dict")
    clean = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or len(key) > 64:
            raise NotificationError("invalid metadata key")
        if isinstance(value, bool) or isinstance(value, int):
            clean[key] = value
        elif isinstance(value, str):
            if len(value) > 140:
                raise NotificationError("metadata value too long")
            clean[key] = value
        else:
            raise NotificationError("unsupported metadata value type")
    if len(str(clean)) > MAX_METADATA_BYTES:
        raise NotificationError("metadata too large")
    return clean


def notify(*, recipient, category, title, body="", kind="", metadata=None,
           dedup_key=None):
    """Create one in-app notification. Never raises.

    Returns the created Notification, an existing duplicate when dedup_key
    matches (idempotency), or None when creation failed (failure is audited).
    """
    try:
        if category not in Category.values:
            raise NotificationError(f"unknown category {category!r}")
        if kind:
            if not kind.replace("_", "").isalnum():
                raise NotificationError(f"invalid kind {kind!r}")
        safe_meta = _sanitize_metadata(metadata or {})
        # B7: per-category opt-out. Mandatory kinds are never suppressed;
        # a disabled category silently drops FUTURE events only (existing
        # rows are untouched).
        if kind not in MANDATORY_NOTIFICATION_KINDS and \
                not _category_enabled(recipient, category):
            return None
        return _create(recipient=recipient, category=category, title=title,
                       body=body[:500], kind=kind, metadata=safe_meta,
                       dedup_key=dedup_key)
    except Exception as exc:  # noqa: BLE001 — notification must never be critical
        logger.warning("notification failed: %s", exc)
        audit(action="NOTIFICATION_ERROR",
              metadata={
                  "source": "notifications.notify",
                  "kind": str(kind)[:60],
                  "category": str(category)[:30],
                  "recipient_id": getattr(recipient, "pk", None),
                  "dedup_key": str(dedup_key)[:180],
                  "exception": type(exc).__name__,
              })
        return None


@transaction.atomic
def _create(*, recipient, category, title, body, kind, metadata, dedup_key):
    try:
        with transaction.atomic():
            return Notification.objects.create(
                recipient=recipient, category=category, title=title,
                body=body, kind=kind, metadata=metadata, dedup_key=dedup_key,
            )
    except IntegrityError:
        # concurrent insert of the same semantic event — idempotent replay
        # (scoped to this recipient: keys are unique per user, not globally)
        existing = Notification.objects.filter(
            dedup_key=dedup_key, recipient=recipient).first()
        if existing is None:
            raise
        return existing


def _category_enabled(recipient, category):
    """Missing row = enabled. Never raises (notify() stays non-critical)."""
    try:
        return NotificationPreference.objects.filter(
            user=recipient, category=category).values_list(
            "enabled", flat=True).first() is not False
    except Exception:  # noqa: BLE001
        return True


def set_category_preference(*, actor, category, enabled):
    """Opt in/out of a notification category. Returns the preference row."""
    if category not in Category.values:
        raise NotificationError(f"unknown category {category!r}")
    pref, _ = NotificationPreference.objects.update_or_create(
        user=actor, category=category, defaults={"enabled": bool(enabled)})
    audit(actor=actor, action="NOTIFICATION_PREFERENCES_CHANGED",
          metadata={"category": category, "enabled": bool(enabled)})
    return pref


def mark_read(notification):
    """Idempotent read marking with read_at consistency."""
    if notification.read:
        return notification
    notification.read = True
    notification.read_at = timezone.now()
    notification.save(update_fields=["read", "read_at"])
    return notification


def mark_all_read(recipient):
    return Notification.objects.filter(recipient=recipient, read=False).update(
        read=True, read_at=timezone.now())
