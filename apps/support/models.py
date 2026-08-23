from django.conf import settings
from django.db import models


class Ticket(models.Model):
    PRIORITIES = [("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High")]
    STATUSES = [("OPEN", "Open"), ("IN_PROGRESS", "In Progress"), ("RESOLVED", "Resolved"), ("CLOSED", "Closed")]

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tickets")
    subject = models.CharField(max_length=180)
    body = models.TextField()
    priority = models.CharField(max_length=8, choices=PRIORITIES, default="MEDIUM")
    status = models.CharField(max_length=12, choices=STATUSES, default="OPEN")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_tickets")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    body = models.TextField()
    internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
