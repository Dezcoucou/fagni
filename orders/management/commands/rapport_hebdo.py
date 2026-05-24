"""
FAGNI — Rapport hebdomadaire automatique
Usage : python manage.py rapport_hebdo
Cron  : tous les lundis 7h00 (PythonAnywhere Tasks)
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal


class Command(BaseCommand):
    help = "Génère le rapport hebdomadaire FAGNI"

    def handle(self, *args, **kwargs):
        from orders.models import Order, FagniEvent

        now  = timezone.now()
        fin  = now.replace(hour=0, minute=0, second=0, microsecond=0)
        deb  = fin - timedelta(days=7)

        orders = Order.objects.filter(created_at__gte=deb, created_at__lt=fin)

        total        = orders.count()
        livrees      = orders.filter(status='done').count()
        annulees     = orders.filter(status='cancelled').count()
        en_cours     = total - livrees - annulees

        revenu_brut  = sum(o.total_client_ttc or 0 for o in orders.filter(status='done'))
        marge_totale = sum(o.margin_net or 0 for o in orders.filter(status='done'))
        marge_moy    = round(marge_totale / livrees, 0) if livrees else 0

        rentables    = orders.filter(profitability_label='rentable').count()
        limites      = orders.filter(profitability_label='limite').count()
        a_perte      = orders.filter(profitability_label='a_perte').count()

        events       = FagniEvent.objects.filter(created_at__gte=deb, created_at__lt=fin)
        incidents    = events.filter(event_type='incident.reported').count()

        rapport = f"""
╔══════════════════════════════════════════╗
║     FAGNI — RAPPORT HEBDOMADAIRE         ║
║  {deb:%d/%m/%Y} → {fin:%d/%m/%Y}              ║
╠══════════════════════════════════════════╣
║ COMMANDES                                ║
║  Total créées    : {total:<5}                 ║
║  Livrées         : {livrees:<5}                 ║
║  En cours        : {en_cours:<5}                 ║
║  Annulées        : {annulees:<5}                 ║
║  Taux complétion : {round(livrees/total*100) if total else 0:<5}%                ║
╠══════════════════════════════════════════╣
║ RENTABILITÉ                              ║
║  Revenu brut     : {int(revenu_brut):,} FCFA          ║
║  Marge totale    : {int(marge_totale):,} FCFA          ║
║  Marge / commande: {int(marge_moy):,} FCFA          ║
║  Rentables       : {rentables:<5}                 ║
║  Limites         : {limites:<5}                 ║
║  À perte         : {a_perte:<5}                 ║
╠══════════════════════════════════════════╣
║ QUALITÉ                                  ║
║  Incidents       : {incidents:<5}                 ║
║  Events loggés   : {events.count():<5}                 ║
╚══════════════════════════════════════════╝
"""
        self.stdout.write(rapport)

        # Sauvegarder dans un fichier log
        import os
        log_dir = "/tmp/fagni_rapports"
        os.makedirs(log_dir, exist_ok=True)
        log_file = f"{log_dir}/rapport_{fin:%Y%m%d}.txt"
        with open(log_file, 'w') as f:
            f.write(rapport)
        self.stdout.write(f"✅ Rapport sauvegardé : {log_file}")
