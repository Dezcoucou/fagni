from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Crée le superuser configuré si inexistant"

    def handle(self, *args, **options):
        User = get_user_model()

        username = (getattr(settings, "DEFAULT_ADMIN_USERNAME", "") or "").strip()
        email = (getattr(settings, "DEFAULT_ADMIN_EMAIL", "") or "").strip()
        password = (getattr(settings, "DEFAULT_ADMIN_PASSWORD", "") or "").strip()

        if not username:
            raise CommandError("DEFAULT_ADMIN_USERNAME absent.")

        if not password:
            raise CommandError(
                "DEFAULT_ADMIN_PASSWORD absent : création du superuser refusée."
            )

        if User.objects.filter(username=username).exists():
            self.stdout.write(
                self.style.WARNING(
                    f"Le superuser {username} existe déjà."
                )
            )
            return

        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Superuser {username} créé avec succès."
            )
        )
