from django.apps import AppConfig

class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'
    verbose_name = "Contenu"

    def ready(self):
        from . import labels_fr  # noqa: F401
