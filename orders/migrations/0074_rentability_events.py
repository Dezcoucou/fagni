from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0073_add_articles_count_payment_expires_awaiting"),
    ]

    operations = [

        # ── Champs rentabilité sur Order ──────────────────────────
        migrations.AddField("order", "cost_driver_pickup",
            models.IntegerField(default=0,
                verbose_name="Coût livreur collecte (FCFA)")),
        migrations.AddField("order", "cost_driver_delivery",
            models.IntegerField(default=0,
                verbose_name="Coût livreur livraison (FCFA)")),
        migrations.AddField("order", "cost_pressing",
            models.IntegerField(default=0,
                verbose_name="Coût pressing (FCFA)")),
        migrations.AddField("order", "margin_net",
            models.IntegerField(default=0,
                verbose_name="Marge nette FAGNI (FCFA)")),
        migrations.AddField("order", "profitability_score",
            models.DecimalField(max_digits=5, decimal_places=2,
                null=True, blank=True,
                verbose_name="Score rentabilité (%)")),
        migrations.AddField("order", "profitability_label",
            models.CharField(max_length=20, null=True, blank=True,
                choices=[
                    ("rentable", "Rentable"),
                    ("limite", "Limite"),
                    ("a_perte", "À perte"),
                ],
                verbose_name="Rentabilité")),

        # ── Modèle FagniEvent ─────────────────────────────────────
        migrations.CreateModel(
            name="FagniEvent",
            fields=[
                ("id", models.BigAutoField(primary_key=True)),
                ("order", models.ForeignKey(
                    "orders.Order",
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="events",
                    null=True, blank=True,
                    verbose_name="Commande")),
                ("event_type", models.CharField(max_length=60,
                    verbose_name="Type événement",
                    choices=[
                        ("order.created",       "Commande créée"),
                        ("payment.paid",        "Paiement reçu"),
                        ("pickup.assigned",     "Livreur collecte assigné"),
                        ("pickup.started",      "Collecte démarrée"),
                        ("pickup.done",         "Collecte confirmée"),
                        ("pressing.received",   "Pressing reçu"),
                        ("pressing.done",       "Pressing terminé"),
                        ("delivery.assigned",   "Livreur livraison assigné"),
                        ("delivery.started",    "Livraison démarrée"),
                        ("delivery.done",       "Livraison confirmée"),
                        ("order.cancelled",     "Commande annulée"),
                        ("incident.reported",   "Incident signalé"),
                    ])),
                ("actor_type", models.CharField(max_length=20,
                    choices=[
                        ("client",  "Client"),
                        ("driver",  "Livreur"),
                        ("partner", "Pressing"),
                        ("ops",     "OPS"),
                        ("system",  "Système"),
                    ])),
                ("actor_id", models.IntegerField(null=True, blank=True)),
                ("payload_json", models.JSONField(default=dict, blank=True)),
                ("created_at", models.DateTimeField(
                    default=django.utils.timezone.now,
                    db_index=True)),
            ],
            options={
                "verbose_name": "Événement FAGNI",
                "verbose_name_plural": "Événements FAGNI",
                "ordering": ["-created_at"],
            },
        ),
    ]
