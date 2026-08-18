from django.apps import AppConfig


class OrdersConfig(AppConfig):
    verbose_name = 'Commandes'
    default_auto_field = "django.db.models.BigAutoField"
    name = "orders"

    def ready(self):
        """
        Hook appelé quand Django a chargé toutes les apps.
        Ici on se contente de brancher un signal post_migrate
        qui créera un superuser par défaut si besoin.
        """
        from django.db.models.signals import post_migrate
        from django.conf import settings
        from django.contrib.auth import get_user_model

        def ensure_default_superuser(sender, **kwargs):
            """
            Création auto d'un admin sur chaque migrate
            (local + Render).
            On ne le crée que s'il n'existe pas déjà.
            """
            User = get_user_model()

            username = getattr(settings, "DEFAULT_ADMIN_USERNAME", "admin")
            email = getattr(settings, "DEFAULT_ADMIN_EMAIL", "admin@fagni.com")
            password = (
                getattr(settings, "DEFAULT_ADMIN_PASSWORD", "") or ""
            ).strip()

            # Sécurité : ne jamais créer automatiquement un superuser
            # avec un mot de passe par défaut ou vide.
            if not password:
                return

            if not User.objects.filter(username=username).exists():
                User.objects.create_superuser(
                    username=username,
                    email=email,
                    password=password,
                )

        # On connecte le signal après migrations
        post_migrate.connect(ensure_default_superuser, sender=self)

        # Brancher les signaux (DeliveryLeg -> sync Order.status)
        from . import signals  # noqa: F401
