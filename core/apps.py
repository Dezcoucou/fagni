from django.apps import AppConfig


class CoreConfig(AppConfig):
    verbose_name = 'Données de base'
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
