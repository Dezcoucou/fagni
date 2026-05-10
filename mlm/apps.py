from django.apps import AppConfig


class MlmConfig(AppConfig):
    verbose_name = 'Parrainage & MLM'
    default_auto_field = "django.db.models.BigAutoField"
    name = "mlm"

    def ready(self):
        import mlm.signals
