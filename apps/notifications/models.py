from django.conf import settings
from django.db import models


class Category(models.TextChoices):
    SYSTEM = "SYSTEM"
    SECURITY = "SECURITY"
    TRANSFER = "TRANSFER"
    PAYMENT = "PAYMENT"
    CARD = "CARD"


class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    category = models.CharField(max_length=32, choices=Category.choices, default=Category.SYSTEM)
    kind = models.CharField(max_length=64, blank=True, default="")
    title = models.CharField(max_length=140)
    body = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    # Semantic idempotency: recipient+event+operation. Unique PER RECIPIENT
    # (a shared key between two users must never swallow either event);
    # NULL for legacy/seed rows.
    dedup_key = models.CharField(max_length=200, null=True, blank=True)
    read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["recipient", "read"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["recipient", "dedup_key"],
                                    name="uniq_notification_dedup_per_recipient"),
        ]

    def __str__(self):
        return f"{self.category}/{self.kind or '-'} → {self.recipient_id} [{'read' if self.read else 'unread'}]"


class NotificationPreference(models.Model):
    """Per-category opt-out (FASE 6 B7). Missing row = enabled. Mandatory
    kinds (services.MANDATORY_NOTIFICATION_KINDS) ignore this entirely."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="notification_preferences")
    category = models.CharField(max_length=32, choices=Category.choices)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "category"],
                                    name="uniq_notification_pref_user_category"),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.category}={'on' if self.enabled else 'off'}"
