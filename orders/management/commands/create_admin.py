from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = "Crée un superuser par défaut si inexistant"

    def handle(self, *args, **options):
        User = get_user_model()

        username = "admin"
        email = "admin@example.com"
        password = "Admin1234!"

        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(
                username=username,
                email=email,
                password=password
            )
            self.stdout.write(self.style.SUCCESS("Superuser créé : admin / Admin1234!"))
        else:
            self.stdout.write(self.style.WARNING("Le superuser existe déjà."))
