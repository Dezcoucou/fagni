from django.apps import AppConfig


class WalletsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "wallets"

    def ready(self):
        # active les signals (payout -> reconcile)
        from . import signals  # noqa: F401
