from django.apps import AppConfig

class OrdersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'orders'
    verbose_name = "Commandes"

    def ready(self):
        # applique les labels FR dynamiquement
        from . import labels_fr  # noqa: F401
