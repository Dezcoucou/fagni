"""
Management command : expire les offres Crowd dépassées.

Usage :
    python manage.py expire_crowd_offers
    
Peut être lancé par cron toutes les minutes :
    * * * * * cd /path/to/fagni && source venv/bin/activate && python manage.py expire_crowd_offers
"""
from django.core.management.base import BaseCommand
from crowd.expiration import expire_stale_offers


class Command(BaseCommand):
    help = "Expire les offres Crowd dont le timeout est dépassé"

    def handle(self, *args, **options):
        result = expire_stale_offers()
        self.stdout.write(
            f"Offres expirées : {result['expired']} | Erreurs : {result['errors']}"
        )
