from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Immutable audit trail. No updates/deletes permitted by application policy."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events")
    action = models.CharField(max_length=64, db_index=True)
    resource_type = models.CharField(max_length=64, blank=True)
    resource_id = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.objects.filter(pk=self.pk).exists():
            raise ValueError("AuditLog entries are immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog entries cannot be deleted")
