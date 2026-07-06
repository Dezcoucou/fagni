"""
Commande Django : python3 manage.py check_data_dictionary_sync

Compare les champs reellement presents sur les modeles Django avec ce que
documente le FOS-201 (Data Dictionary). Signale :
  - les champs du code absents de la documentation (dette documentaire)
  - les champs documentes qui n'existent plus dans le code (documentation perimee)
"""
from django.core.management.base import BaseCommand
from django.apps import apps


# Champs documentes dans le FOS-201, par modele (nom du champ uniquement).
# Source : FOS-201_Data_Dictionary.docx, version 1.0 du 5 juillet 2026.
EXPECTED_FIELDS = {
    "orders.Order": [
        "id", "code", "customer", "status", "payment_status", "bag_size",
        "total_client_ttc", "delivery_fee", "service_fee",
        "amount_laundry_partner", "fagni_revenue_ht", "margin_net",
        "laundry_partner", "delivery_partner", "pickup_driver", "notes",
        "created_at", "updated_at",
    ],
    "orders.DeliveryLeg": [
        "id", "order", "leg_type", "driver", "status", "driver_amount",
        "client_fee_share", "fagni_margin", "picked_up_lat", "picked_up_lng",
        "delivered_lat", "delivered_lng", "pickup_signature",
        "client_signature", "started_at", "finished_at",
    ],
    "orders.OrderEvidencePhoto": [
        "id", "order", "leg", "actor_type", "actor_id", "kind", "image",
        "caption", "created_at",
    ],
    "orders.FCMToken": ["id", "user_type", "user_id", "token", "created_at", "updated_at"],
    "wallets.Wallet": ["id", "owner_type", "customer", "laundry_partner", "relay_partner",
                        "delivery_partner", "user", "currency", "balance"],
    "wallets.WalletTransaction": [
        "id", "wallet", "order", "leg", "type", "direction", "amount",
        "idempotency_key", "description", "created_at",
    ],
    "partners.DeliveryPartner": [
        "id", "name", "phone", "city", "remuneration_collecte",
        "remuneration_livraison", "wave_number", "is_active",
        "vehicle_type", "cni_number", "photo_cni_url", "photo_profil_url",
    ],
    "partners.LaundryPartner": [
        "id", "name", "phone", "city", "remuneration_collecte",
        "remuneration_livraison", "wave_number", "is_active",
        "partner_type", "partner_score", "level",
    ],
}


class Command(BaseCommand):
    help = "Verifie la synchronisation entre le FOS-201 (Data Dictionary) et les modeles Django reels"

    def handle(self, *args, **options):
        total_undocumented = 0
        total_stale = 0

        for model_path, documented_fields in EXPECTED_FIELDS.items():
            app_label, model_name = model_path.split(".")
            try:
                model = apps.get_model(app_label, model_name)
            except LookupError:
                self.stderr.write(self.style.ERROR(f"MODELE INTROUVABLE: {model_path} (renomme ou supprime ?)"))
                continue

            real_fields = set()
            for f in model._meta.get_fields():
                real_fields.add(f.name)

            documented_set = set(documented_fields)

            undocumented = sorted(real_fields - documented_set)
            stale = sorted(documented_set - real_fields)

            self.stdout.write(f"\n=== {model_path} ===")
            if not undocumented and not stale:
                self.stdout.write(self.style.SUCCESS("  OK - synchronise, aucun ecart."))
            if undocumented:
                total_undocumented += len(undocumented)
                self.stdout.write(self.style.WARNING(f"  Champs du CODE absents du FOS-201 ({len(undocumented)}):"))
                for f in undocumented:
                    self.stdout.write(f"    - {f}")
            if stale:
                total_stale += len(stale)
                self.stdout.write(self.style.ERROR(f"  Champs du FOS-201 absents du CODE ({len(stale)}) - documentation perimee:"))
                for f in stale:
                    self.stdout.write(f"    - {f}")

        self.stdout.write(f"\n=== RESUME ===")
        self.stdout.write(f"Champs non documentes: {total_undocumented}")
        self.stdout.write(f"Champs documentes obsoletes: {total_stale}")
        if total_undocumented == 0 and total_stale == 0:
            self.stdout.write(self.style.SUCCESS("FOS-201 parfaitement synchronise avec le schema reel."))
        else:
            self.stdout.write(self.style.WARNING("FOS-201 necessite une mise a jour (voir details ci-dessus)."))
