"""Fraud & Risk Engine domain models.

Concepts are kept separate on purpose (spec §17):
- RiskEvaluation: one evaluation attempt of one operation.
- RiskSignal: a collected FACT attached to an evaluation.
- RiskRule: versioned, explainable rule definition.
- FraudAlert: something requiring visibility (not fraud confirmation).
- FraudCase: an investigation; the ONLY place where fraud is confirmed.

Risk score never equals confirmed fraud (INVARIANT 6).
"""
import uuid

from django.conf import settings
from django.db import models


class RiskEvaluation(models.Model):
    """Immutable record of one risk evaluation. Historical rows retain the
    policy/ruleset versions that produced them (INVARIANT 3)."""

    class RiskLevel(models.TextChoices):
        LOW = "LOW"
        MEDIUM = "MEDIUM"
        HIGH = "HIGH"
        CRITICAL = "CRITICAL"

    class Decision(models.TextChoices):
        PENDING = "PENDING", "evaluation in flight"
        ALLOW = "ALLOW"
        CHALLENGE = "CHALLENGE"
        REVIEW = "REVIEW"
        BLOCK = "BLOCK"
        DEFER = "DEFER", "dependency unavailable"

    class EngineMode(models.TextChoices):
        SHADOW = "SHADOW"
        CHALLENGE_ONLY = "CHALLENGE_ONLY"
        ENFORCEMENT = "ENFORCEMENT"

    class Status(models.TextChoices):
        EVALUATING = "EVALUATING"
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"  # engine error; fail-safe behavior applies

    operation_type = models.CharField(max_length=64, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="risk_evaluations",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    resource_reference = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)

    risk_score = models.PositiveSmallIntegerField(null=True, blank=True)
    risk_level = models.CharField(max_length=10, choices=RiskLevel.choices, blank=True)
    decision = models.CharField(max_length=10, choices=Decision.choices, default=Decision.PENDING)

    engine_mode = models.CharField(max_length=20, choices=EngineMode.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.EVALUATING)

    policy_version = models.CharField(max_length=40, blank=True)
    ruleset_version = models.CharField(max_length=40, blank=True)
    triggered_rules = models.JSONField(default=list, blank=True)  # [{rule_id, version, score}]
    signal_values = models.JSONField(default=dict, blank=True)    # {signal_id: value}

    idempotency_key = models.CharField(max_length=200, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="riskevaluation_score_bounds",
                check=models.Q(risk_score__isnull=True) | models.Q(risk_score__lte=100),
            ),
            models.CheckConstraint(
                name="riskevaluation_decision_valid",
                check=~models.Q(decision=""),
            ),
        ]
        indexes = [
            models.Index(fields=["operation_type", "-created_at"]),
            models.Index(fields=["decision", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.operation_type} #{self.pk} -> {self.decision} ({self.risk_score})"


class RiskSignal(models.Model):
    """A collected fact about the evaluated operation. Facts, not rules."""

    evaluation = models.ForeignKey(RiskEvaluation, on_delete=models.CASCADE, related_name="signals")
    signal_id = models.CharField(max_length=64)
    value = models.JSONField(null=True)  # None is a valid fact ("unknown")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["evaluation", "signal_id"], name="uniq_signal_per_evaluation"),
        ]

    def __str__(self):
        return f"{self.signal_id}={self.value!r}"


class RiskRule(models.Model):
    """Versioned rule definition. Lifecycle managed by rule management (Task 18);
    only ACTIVE rules with valid effective windows contribute to scores."""

    class Lifecycle(models.TextChoices):
        DRAFT = "DRAFT"
        TESTING = "TESTING"
        APPROVED = "APPROVED"
        ACTIVE = "ACTIVE"
        RETIRED = "RETIRED"

    rule_id = models.SlugField(max_length=64)  # stable across versions
    version = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=False)
    lifecycle = models.CharField(max_length=10, choices=Lifecycle.choices, default=Lifecycle.DRAFT)

    priority = models.PositiveIntegerField(default=100)
    operation_types = models.JSONField(default=list)   # [] == all operations
    conditions = models.JSONField(default=dict)        # structured, explainable
    score = models.PositiveSmallIntegerField()         # points contributed when triggered
    severity = models.CharField(max_length=10, default="MEDIUM")

    effective_from = models.DateTimeField(null=True, blank=True)
    effective_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["rule_id", "version"], name="uniq_rule_id_version"),
            models.CheckConstraint(name="riskrule_score_bounds", check=models.Q(score__lte=100)),
        ]
        ordering = ["priority", "id"]

    def __str__(self):
        return f"{self.rule_id} v{self.version} [{self.lifecycle}]"


class FraudAlert(models.Model):
    """Requires visibility — not automatically an investigation."""

    class Status(models.TextChoices):
        OPEN = "OPEN"
        ACKNOWLEDGED = "ACKNOWLEDGED"
        ESCALATED = "ESCALATED"
        CLOSED = "CLOSED"

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="engine_alerts",
    )
    evaluation = models.ForeignKey(RiskEvaluation, null=True, blank=True, on_delete=models.SET_NULL)
    alert_type = models.CharField(max_length=64)
    severity = models.CharField(max_length=10, default="MEDIUM")
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.OPEN)
    dedup_key = models.CharField(max_length=200, blank=True, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"ALERT-{self.pk} {self.alert_type}/{self.severity} {self.status}"


def new_case_reference():
    return uuid.uuid4().hex[:16].upper()


class FraudCase(models.Model):
    """An investigation. The only workflow allowed to set CONFIRMED_FRAUD (INVARIANT 6)."""

    class Status(models.TextChoices):
        OPEN = "OPEN"
        INVESTIGATING = "INVESTIGATING"
        WAITING_CUSTOMER = "WAITING_CUSTOMER"
        ESCALATED = "ESCALATED"
        CONFIRMED_FRAUD = "CONFIRMED_FRAUD"
        FALSE_POSITIVE = "FALSE_POSITIVE"
        CLOSED = "CLOSED"

    case_reference = models.CharField(max_length=32, unique=True, default=new_case_reference)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="fraud_cases")
    severity = models.CharField(max_length=10, default="MEDIUM")
    assigned_analyst = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    alerts = models.ManyToManyField(FraudAlert, blank=True, related_name="cases")
    summary = models.TextField(blank=True)
    decision_reason = models.TextField(blank=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="fraudcase_closed_requires_reason",
                check=(~models.Q(status="CLOSED")) | (~models.Q(decision_reason="")),
            ),
        ]

    def __str__(self):
        return f"CASE-{self.case_reference} [{self.status}]"
