from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from orders.models import Order
from logistics.adapters import (
    get_order_source_address_v2,
    get_order_destination_address_v2,
    get_order_contact_name,
    get_order_contact_phone,
    get_or_create_smoke_partner,
)
from logistics.services import create_mission_for_order, start_mission, complete_mission
from production.services import (
    create_partner_job,
    mark_partner_job_received,
    mark_partner_job_ready,
    record_weighing,
)
from tracking.services import create_tracking_event, create_incident
from pricing.services import create_estimated_quote

class Command(BaseCommand):
    help = "Smoke test V2 FAGNI sur une commande existante"

    def add_arguments(self, parser):
        parser.add_argument(
            "--order-id",
            type=int,
            help="ID d'une commande existante à utiliser pour le smoke test",
        )
        parser.add_argument(
            "--create-incident",
            action="store_true",
            help="Créer aussi un incident de test",
        )

    def handle(self, *args, **options):
        order_id = options.get("order_id")
        create_incident_flag = options.get("create_incident", False)

        if order_id:
            order = Order.objects.filter(pk=order_id).first()
            if not order:
                raise CommandError(f"Aucune commande trouvée avec id={order_id}")
        else:
            order = Order.objects.order_by("-id").first()
            if not order:
                raise CommandError(
                    "Aucune commande existante trouvée dans orders.Order. "
                    "Crée d'abord au moins une commande réelle."
                )

        self.stdout.write(self.style.NOTICE(f"Commande utilisée : id={order.id}"))

        source_address = get_order_source_address_v2(order)
        destination_address = get_order_destination_address_v2(order)

        if source_address is None or destination_address is None:
            raise CommandError(
                "Impossible de trouver ou créer des adresses V2 compatibles."
            )

        partner = get_or_create_smoke_partner()
        self.stdout.write(f"Partenaire utilisé : {partner.name}")

        self.stdout.write(self.style.NOTICE("1) Création mission V2..."))
        mission = create_mission_for_order(
            order=order,
            mission_type="pickup_from_customer",
            source_address=source_address,
            destination_address=destination_address,
            contact_name=get_order_contact_name(order),
            contact_phone=get_order_contact_phone(order),
            priority="normal",
            instructions="Mission générée par smoke_v2",
            sequence_index=1,
        )
        self.stdout.write(self.style.SUCCESS(f"Mission créée : {mission.code}"))

        self.stdout.write(self.style.NOTICE("2) Démarrage mission..."))
        mission = start_mission(mission=mission, notes="Départ smoke test")
        self.stdout.write(self.style.SUCCESS(f"Mission démarrée : {mission.status}"))

        self.stdout.write(self.style.NOTICE("3) Clôture mission..."))
        mission = complete_mission(
            mission=mission,
            notes="Mission terminée par smoke_v2",
            action_type="completed",
        )
        self.stdout.write(self.style.SUCCESS(f"Mission terminée : {mission.status}"))

        self.stdout.write(self.style.NOTICE("4) Création PartnerJob..."))
        partner_job = create_partner_job(
            order=order,
            partner=partner,
            notes="Job créé par smoke_v2",
        )
        self.stdout.write(self.style.SUCCESS(f"PartnerJob créé : {partner_job.code}"))

        self.stdout.write(self.style.NOTICE("5) Réception partenaire..."))
        partner_job = mark_partner_job_received(
            partner_job=partner_job,
            notes="Réception confirmée par smoke_v2",
        )
        self.stdout.write(self.style.SUCCESS(f"PartnerJob reçu : {partner_job.status}"))

        self.stdout.write(self.style.NOTICE("6) Enregistrement pesée..."))
        weighing = record_weighing(
            order=order,
            partner_job=partner_job,
            mission=mission,
            net_weight=Decimal("3.50"),
            gross_weight=Decimal("3.80"),
            weighing_stage="partner_reception",
            performed_by_role="partner",
            unit="kg",
            notes="Pesée smoke test",
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Pesée créée : net={weighing.net_weight}{weighing.unit}"
            )
        )

        self.stdout.write(self.style.NOTICE("7) Passage PartnerJob à ready..."))
        partner_job = mark_partner_job_ready(
            partner_job=partner_job,
            notes="Prêt pour récupération - smoke_v2",
        )
        self.stdout.write(self.style.SUCCESS(f"PartnerJob prêt : {partner_job.status}"))

        self.stdout.write(self.style.NOTICE("8) Tracking event..."))
        event = create_tracking_event(
            order=order,
            mission=mission,
            partner_job=partner_job,
            event_type="production_ready",
            actor_role="system",
            title="Smoke V2 terminé",
            description="Flux minimal V2 exécuté avec succès",
            status_before="processing",
            status_after="ready",
            metadata_json={
                "command": "smoke_v2",
                "order_id": order.id,
                "mission_code": mission.code,
                "partner_job_code": partner_job.code,
            },
        )
        self.stdout.write(self.style.SUCCESS(f"TrackingEvent créé : {event.id}"))

        self.stdout.write(self.style.NOTICE("9) Devis estimatif..."))
        quote = create_estimated_quote(
            order=order,
            notes="Devis généré par smoke_v2",
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"PriceQuote créé : id={quote.id}, total={quote.total_amount} {quote.currency}"
            )
        )

        if create_incident_flag:
            self.stdout.write(self.style.NOTICE("10) Incident de test..."))
            incident = create_incident(
                order=order,
                mission=mission,
                partner_job=partner_job,
                incident_type="delay",
                title="Incident de test smoke_v2",
                description="Incident généré automatiquement pour vérifier le flux V2.",
                severity="low",
            )
            self.stdout.write(self.style.SUCCESS(f"Incident créé : {incident.id}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Smoke V2 terminé avec succès."))
        self.stdout.write(f"Résumé : order={order.id}, mission={mission.code}, partner_job={partner_job.code}, quote={quote.id}")
