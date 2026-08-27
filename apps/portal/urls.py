from django.urls import path

from . import views

urlpatterns = [
    # public marketing
    path("healthz/", views.healthz, name="portal_healthz"),
    path("", views.home, name="portal_home"),
    path("personal/", views.personal, name="portal_personal"),
    path("business/", views.business, name="portal_business"),
    path("cards/", views.cards_page, name="portal_cards"),
    path("investments/", views.investments_page, name="portal_investments"),
    path("loans/", views.loans_page, name="portal_loans"),
    path("loans/simulate/", views.loan_simulate, name="portal_loan_simulate"),
    path("security/", views.security_page, name="portal_security"),
    path("help/", views.help_page, name="portal_help"),

    # account opening
    path("open-account/", views.open_account_landing, name="portal_open_account"),
    path("open-account/<int:step>/", views.wizard, name="portal_wizard_step"),
    path("open-account/<int:step>/next/", views.wizard_next, name="portal_wizard_next"),
    path("application/save-later/", views.save_and_continue_later, name="portal_save_later"),
    path("application/resume/", views.application_resume, name="portal_resume"),
    path("application/submit/", views.submit_view, name="portal_submit"),
    path("application/status/<str:reference>/", views.application_status, name="portal_application_status"),

    # manager access (separate institutional flow)
    path("manager/login/", views.manager_login, name="manager_login"),
    path("manager/login/otp/", views.manager_login_otp, name="manager_login_otp"),
    path("manage/applications/", views.manage_applications, name="portal_manage_applications"),
    path("manage/applications/decide/", views.decide_application_view, name="portal_decide_application"),
]
