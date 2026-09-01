"""Helper partagé pour seeder le catalogue V2 dans les tests."""
from django.core.management import call_command


def seed_catalog_v2():
    """Seed le catalogue V2 minimal pour les tests qui créent des commandes via l'API."""
    call_command("seed_v2", verbosity=0)
