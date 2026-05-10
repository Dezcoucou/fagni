from django.apps import AppConfig


class ProductionConfig(AppConfig):
    verbose_name = 'Production'
    default_auto_field = "django.db.models.BigAutoField"
    name = "production"
