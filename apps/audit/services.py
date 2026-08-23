from .models import AuditLog


def record(actor=None, action="", request=None, resource=None, metadata=None):
    """Create an immutable audit event."""
    ip = None
    device = ""
    if request:
        ip = request.META.get("REMOTE_ADDR")
        device = (request.META.get("HTTP_USER_AGENT") or "")[:200]
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        resource_type=resource.__class__.__name__ if resource is not None and not isinstance(resource, str) else "",
        resource_id=str(getattr(resource, "pk", resource) or ""),
        ip_address=ip,
        device=device,
        metadata=metadata or {},
    )
