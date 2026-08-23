from django.urls import path

from . import views

urlpatterns = [
    path("manage/", views.manager_dashboard, name="manager_dashboard"),
    path("manage/customers/", views.customer_search, name="manager_customers"),
    path("manage/customers/<int:customer_id>/", views.customer_360, name="manager_customer360"),
    path("manage/onboarding/", views.onboard_customer, name="manager_onboarding"),
    path("manage/open-account/", views.open_account, name="manager_open_account"),
    path("manage/approvals/", views.approvals_queue, name="manager_approvals"),
    path("manage/approvals/decide/", views.approve_request, name="manager_decide"),
    path("manage/restrictions/", views.restrictions_view, name="manager_restrictions"),
    path("manage/restrictions/apply/", views.apply_restriction, name="manager_apply_restriction"),
    path("manage/restrictions/lift/", views.lift_restriction_view, name="manager_lift_restriction"),
    path("manage/customers/<int:customer_id>/notes/", views.add_note, name="manager_add_note"),
    path("manage/customers/<int:customer_id>/requests/", views.create_service_request, name="manager_service_request"),
]
urlpatterns += [
    path("manage/card-requests/", views.card_requests_view, name="manager_card_requests"),
    path("manage/card-requests/<int:req_id>/decide/", views.decide_card_request, name="manager_decide_card"),
]
