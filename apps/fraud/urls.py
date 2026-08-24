from django.urls import path

from . import challenge_views, security_ops, views

app_name = "fraud"

urlpatterns = [
    path("fraud/", views.dashboard, name="dashboard"),
    path("security/challenge/<int:challenge_id>/", challenge_views.challenge_detail, name="stepup_challenge"),
    path("fraud/alerts/", views.alert_queue, name="alert_queue"),
    path("fraud/cases/<int:case_id>/", views.case_view, name="case_view"),
    path("fraud/alerts/<int:alert_id>/acknowledge/", views.acknowledge_alert, name="acknowledge_alert"),
    path("fraud/alerts/<int:alert_id>/open-case/", views.open_case_from_alert, name="open_case_from_alert"),
    path("fraud/cases/<int:case_id>/decide/", views.decide_case, name="decide_case"),
    # FASE 4.4 — Security Operations console (staff only)
    path("secops/health/", security_ops.engine_health, name="secops_health"),
]
