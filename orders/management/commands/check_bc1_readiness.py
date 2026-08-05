from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = (
        "Diagnostic non technique pour BC1 (auto-affectation pressing + livreur "
        "a la creation d'une commande client) : verifie, sans lire aucun log, "
        "si l'auto-affectation PEUT fonctionner en l'etat actuel de la config, "
        "et donne la raison exacte en francais si ce n'est pas le cas."
    )

    def handle(self, *args, **opts):
        from partners.models import LaundryPartner, DeliveryPartner
        from orders.config_models import AssignmentSettings

        w = self.stdout.write
        w("")
        w(self.style.WARNING("CHECK BC1 READINESS"))
        w("")

        flag_on = bool(getattr(settings, "AUTO_ASSIGN_ON_CLIENT_ORDER", False))
        if flag_on:
            w(self.style.SUCCESS("✅ Flag AUTO_ASSIGN_ON_CLIENT_ORDER : ACTIVE"))
        else:
            w(self.style.ERROR("⛔ Flag AUTO_ASSIGN_ON_CLIENT_ORDER : DESACTIVE"))
            w("   → BC1 ne s'execute jamais tant que ce flag Render n'est pas mis a true.")
            w("")
            w(self.style.ERROR("⛔ BC1 BLOQUE"))
            return

        cfg = AssignmentSettings.get_solo()

        laundry_active = LaundryPartner.objects.filter(is_active=True)
        laundry_active_count = laundry_active.count()
        laundry_geo_count = laundry_active.filter(
            latitude__isnull=False, longitude__isnull=False
        ).count()

        driver_active = DeliveryPartner.objects.filter(is_active=True)
        driver_active_count = driver_active.count()
        driver_geo_count = driver_active.filter(
            latitude__isnull=False, longitude__isnull=False
        ).count()

        blocking_reasons = []

        w("")
        w("— Pressing (blanchisserie) —")
        w(f"   Mode de selection : {cfg.laundry_selection_mode}")
        w(f"   Pressings actifs  : {laundry_active_count} (dont {laundry_geo_count} avec GPS renseigne)")
        if cfg.laundry_selection_mode == "manual":
            blocking_reasons.append(
                "Pressing : le mode de selection est sur \"Selection manuelle\" dans "
                "Admin > Reglages d'affectation. Tant que ce mode reste sur manuel, "
                "BC1 n'affectera JAMAIS aucun pressing, quel que soit le nombre de "
                "pressings actifs. Il faut le repasser sur \"Blanchisserie la plus proche\" "
                "(ou \"prioritaire\") pour autoriser l'auto-affectation."
            )
            w(self.style.ERROR("   ⛔ BLOQUANT : sélection manuelle forcée, aucun pressing ne sera jamais auto-affecté."))
        elif laundry_active_count == 0:
            blocking_reasons.append(
                "Pressing : aucun pressing actif (\"Actif\" décoché ou absent) dans Admin > Blanchisseries."
            )
            w(self.style.ERROR("   ⛔ BLOQUANT : aucun pressing actif."))
        else:
            w(self.style.SUCCESS("   ✅ Pressing : l'auto-affectation peut fonctionner."))

        w("")
        w("— Livreur (collecte) —")
        w(f"   Mode d'assignation : {cfg.driver_assignment_mode}")
        w(f"   Rayon max (km)      : {cfg.driver_radius_km}")
        w(f"   Livreurs actifs     : {driver_active_count} (dont {driver_geo_count} avec GPS renseigne)")
        if cfg.driver_assignment_mode == "manual":
            blocking_reasons.append(
                "Livreur : le mode d'assignation est sur \"Manuelle\" dans "
                "Admin > Reglages d'affectation. Tant que ce mode reste sur manuel, "
                "BC1 n'affectera JAMAIS aucun livreur, quel que soit le nombre de "
                "livreurs actifs. Il faut le repasser sur \"Automatique\" (ou \"Hybride\") "
                "pour autoriser l'auto-affectation."
            )
            w(self.style.ERROR("   ⛔ BLOQUANT : assignation manuelle forcée, aucun livreur ne sera jamais auto-affecté."))
        elif driver_active_count == 0:
            blocking_reasons.append(
                "Livreur : aucun livreur actif (\"Actif\" décoché ou absent) dans Admin > Livreurs."
            )
            w(self.style.ERROR("   ⛔ BLOQUANT : aucun livreur actif."))
        else:
            w(self.style.SUCCESS("   ✅ Livreur : l'auto-affectation peut fonctionner (sous réserve du rayon/charge par commande)."))

        w("")
        if blocking_reasons:
            w(self.style.ERROR(f"⛔ BC1 BLOQUE — {len(blocking_reasons)} probleme(s) :"))
            for reason in blocking_reasons:
                w(f"   - {reason}")
        else:
            w(self.style.SUCCESS("✅ BC1 PRET — aucune condition bloquante detectee dans la configuration."))
        w("")
