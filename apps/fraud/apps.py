from django.apps import AppConfig


class FraudConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.fraud"
    verbose_name = "Fraud & Risk Engine"

    def ready(self):
        # ensure all signal modules are registered at startup
        from . import (  # noqa: F401
            ato,   # registers the ATO_CORRELATION_POINTS signal bridge
            signals,
            signals_velocity,
            signals_beneficiary,
            signals_auth,
            signals_card,
        )
