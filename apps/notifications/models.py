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
    # Semantic idempotency: recipient+event+operation. Unique in the database;
    # NULL for legacy/seed rows (Postgres allows multiple NULLs).
    dedup_key = models.CharField(max_length=200, null=True, blank=True, unique=True)
    read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["recipient", "read"]),
        ]

    def __str__(self):
        return f"{self.category}/{self.kind or '-'} → {self.recipient_id} [{'read' if self.read else 'unread'}]"
