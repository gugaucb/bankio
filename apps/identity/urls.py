from django.urls import include, path

from . import app_views, views

urlpatterns = [
    path("", include("apps.portal.urls")),
    path("app/", views.dashboard_view, name="dashboard"),
    path("app/analytics/", app_views.analytics, name="app_analytics"),
    path("app/accounts/", app_views.accounts_view, name="app_accounts"),
    path("app/transactions/", app_views.transactions_view, name="app_transactions"),
    path("app/investments/", app_views.investments_view, name="app_investments"),
    path("app/cards/", app_views.cards_view, name="app_cards"),
    path("app/security/", app_views.security_view, name="app_security"),
    path("app/settings/", app_views.settings_view, name="app_settings"),
    path("login/", views.login_view, name="login"),
    path("otp/", views.otp_verify_view, name="otp_verify"),
    path("logout/", views.logout_view, name="logout"),
    path("", include("apps.transfers.urls")),
    path("", include("apps.managerops.urls")),
]
