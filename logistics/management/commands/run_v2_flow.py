from django.core.management.base import BaseCommand, CommandError

from orders.models import Order
from logistics.orchestrator import run_minimal_v2_flow


class Command(BaseCommand):
    help = "Exécute le flux minimal orchestré V2 sur une commande existante"

    def add_arguments(self, parser):
        parser.add_argument(
            "--order-id",
            type=int,
            help="ID d'une commande existante",
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
                raise CommandError("Aucune commande existante trouvée.")

        self.stdout.write(self.style.NOTICE(f"Commande utilisée : id={order.id}"))

        result = run_minimal_v2_flow(
            order=order,
            create_incident_flag=create_incident_flag,
        )

        mission = result["mission"]
        partner_job = result["partner_job"]
        weighing_record = result["weighing_record"]
        quote = result["quote"]
        incident = result["incident"]

        self.stdout.write(self.style.SUCCESS(f"Mission : {mission.code} [{mission.status}]"))
        self.stdout.write(self.style.SUCCESS(f"PartnerJob : {partner_job.code} [{partner_job.status}]"))
        self.stdout.write(
            self.style.SUCCESS(
                f"Weighing : {weighing_record.net_weight}{weighing_record.unit} ({weighing_record.weighing_stage})"
            )
        )
        self.stdout.write(self.style.SUCCESS(f"Quote : #{quote.id} total={quote.total_amount} {quote.currency}"))

        if incident:
            self.stdout.write(self.style.SUCCESS(f"Incident : #{incident.id} [{incident.status}]"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("run_v2_flow terminé avec succès."))
