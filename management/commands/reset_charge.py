# fagni/management/commands/reset_charge.py
from django.core.management.base import BaseCommand
from fagni.models import Partenaire

class Command(BaseCommand):
    help = "Remet charge_du_jour à 0 pour tous les partenaires"

    def handle(self, *args, **options):
        updated = Partenaire.objects.update(charge_du_jour=0)
        self.stdout.write(self.style.SUCCESS(f"Charges du jour réinitialisées pour {updated} partenaire(s)."))