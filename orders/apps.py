from django.apps import AppConfig
from django.db.models.signals import post_migrate


class OrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "orders"

    def ready(self):
        """
        Connecte un signal post_migrate pour créer automatiquement
        un superuser 'admin' une fois que les migrations sont appliquées.
        """
        from django.contrib.auth import get_user_model
        from django.db.utils import OperationalError, ProgrammingError

        User = get_user_model()

        def create_default_admin(sender, **kwargs):
            try:
                # On évite de toucher la DB si les tables ne sont pas prêtes
                if not User.objects.filter(username="admin").exists():
                    User.objects.create_superuser(
                        username="admin",
                        password="Admin1234!",
                        email="admin@fagni.com",
                    )
            except (OperationalError, ProgrammingError):
                # Si la DB n'est pas encore prête (premier démarrage foireux, etc.),
                # on ignore et on ne bloque pas le processus.
                pass

        # On connecte le signal sur cette app
        post_migrate.connect(create_default_admin, sender=self)
