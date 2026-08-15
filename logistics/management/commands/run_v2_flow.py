from django.core.management.base import BaseCommand, CommandError

from logistics.orchestrator import (
    ServiceExecutionOrchestrationError,
    run_minimal_v2_flow,
)
from orders.models import Order
from services.models import ServiceExecution


class Command(BaseCommand):
    help = "Exécute le flux minimal orchestré V2 sur une commande existante"

    def add_arguments(self, parser):
        parser.add_argument(
            "--order-id",
            type=int,
            help="ID d'une commande existante",
        )

        parser.add_argument(
            "--service-execution-id",
            type=int,
            help=(
                "ID de la ServiceExecution à exécuter. "
                "Obligatoire lorsqu'une commande possède plusieurs "
                "ServiceExecution et qu'aucune résolution automatique "
                "n'est possible."
            ),
        )

        parser.add_argument(
            "--create-incident",
            action="store_true",
            help="Créer aussi un incident de test",
        )

    def handle(self, *args, **options):
        order_id = options.get("order_id")
        service_execution_id = options.get("service_execution_id")
        create_incident_flag = options.get(
            "create_incident",
            False,
        )

        # ---------------------------------------------------------
        # ORDER
        # ---------------------------------------------------------
        if order_id:
            order = (
                Order.objects
                .filter(pk=order_id)
                .first()
            )

            if not order:
                raise CommandError(
                    f"Aucune commande trouvée avec id={order_id}"
                )
        else:
            order = (
                Order.objects
                .order_by("-id")
                .first()
            )

            if not order:
                raise CommandError(
                    "Aucune commande existante trouvée."
                )

        self.stdout.write(
            self.style.NOTICE(
                f"Commande utilisée : id={order.id}"
            )
        )

        # ---------------------------------------------------------
        # SERVICE EXECUTION EXPLICITE
        # ---------------------------------------------------------
        service_execution = None

        if service_execution_id is not None:
            service_execution = (
                ServiceExecution.objects
                .select_related(
                    "order",
                    "service",
                )
                .filter(
                    pk=service_execution_id,
                )
                .first()
            )

            if service_execution is None:
                raise CommandError(
                    "Aucune ServiceExecution trouvée avec "
                    f"id={service_execution_id}."
                )

            if service_execution.order_id != order.id:
                raise CommandError(
                    "ServiceExecution incompatible : "
                    f"l'exécution #{service_execution.id} appartient "
                    f"à Order #{service_execution.order_id}, "
                    f"pas à Order #{order.id}."
                )

            self.stdout.write(
                self.style.NOTICE(
                    "ServiceExecution demandée : "
                    f"#{service_execution.id} "
                    f"[{service_execution.service.code}]"
                )
            )

        # ---------------------------------------------------------
        # ORCHESTRATION
        # ---------------------------------------------------------
        try:
            result = run_minimal_v2_flow(
                order=order,
                create_incident_flag=create_incident_flag,
                service_execution=service_execution,
            )
        except ServiceExecutionOrchestrationError as exc:
            raise CommandError(str(exc)) from exc

        resolved_execution = result["service_execution"]
        mission = result["mission"]
        partner_job = result["partner_job"]
        weighing_record = result["weighing_record"]
        quote = result["quote"]
        incident = result["incident"]

        # ---------------------------------------------------------
        # RESULTAT
        # ---------------------------------------------------------
        if resolved_execution is not None:
            self.stdout.write(
                self.style.SUCCESS(
                    "ServiceExecution résolue : "
                    f"#{resolved_execution.id} "
                    f"[{resolved_execution.service.code}] "
                    f"status={resolved_execution.status}"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "ServiceExecution : aucune "
                    "(compatibilité legacy)"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Mission : {mission.code} [{mission.status}]"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "PartnerJob : "
                f"{partner_job.code} [{partner_job.status}]"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Weighing : "
                f"{weighing_record.net_weight}"
                f"{weighing_record.unit} "
                f"({weighing_record.weighing_stage})"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Quote : #{quote.id} "
                f"total={quote.total_amount} "
                f"{quote.currency}"
            )
        )

        if incident:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Incident : #{incident.id} "
                    f"[{incident.status}]"
                )
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "run_v2_flow terminé avec succès."
            )
        )
