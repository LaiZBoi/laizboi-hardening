from django.apps import AppConfig


class ApiMobileConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api_mobile'
    verbose_name = 'Mobile API (v1)'

    def ready(self):
        # Register push-notification signals (v3.17.463). Wrapped so a
        # failure here can never block app startup.
        try:
            from . import signals
            signals.register()
        except Exception:
            pass
