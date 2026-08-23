from django.apps import AppConfig


class FraudConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.fraud"
    verbose_name = "Fraud & Risk Engine"

    def ready(self):
        # ensure all signal modules are registered at startup
        from . import signals, signals_velocity, signals_beneficiary  # noqa: F401
