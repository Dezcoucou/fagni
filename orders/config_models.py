# orders/config_models.py

from django.db import models


class SingletonModel(models.Model):
    """
    Base pour les modèles "paramètres uniques".
    On force toujours pk=1 et on fournit get_solo().
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        # Toujours une seule instance : pk=1
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ============================================================
#  A. PARAMÈTRES TARIFS & FISCALITÉ
# ============================================================

class GlobalPricingSettings(SingletonModel):
    """
    Tous les paramètres globaux de pricing & TVA pour FAGNI.
    """

    # --- Service FAGNI ---
    service_fee_percent = models.DecimalField(
        "Taux service FAGNI (%)",
        max_digits=5,
        decimal_places=2,
        default=5.00,
        help_text="Pourcentage appliqué sur le sous-total prestations."
    )
    service_fee_min = models.PositiveIntegerField(
        "Minimum service FAGNI (FCFA)",
        default=500,
        help_text="Montant minimal de service FAGNI si le calcul en % est inférieur."
    )
    laundry_commission_percent = models.DecimalField(
        "Commission FAGNI sur prestation (%)",
        max_digits=5,
        decimal_places=2,
        default=20.00,
        help_text="Pourcentage prélevé par FAGNI sur la prestation blanchisserie."
    )
    rounding_step = models.PositiveIntegerField(
        "Pas d'arrondi (FCFA)",
        default=25,
        help_text="Arrondi des montants (ex : 25 FCFA). 0 = pas d'arrondi spécifique."
    )
    apply_service_on_delivery = models.BooleanField(
        "Appliquer le service aussi sur les frais de livraison",
        default=False,
        help_text="Si coché, la base de calcul inclut aussi les frais de livraison."
    )

    # --- Livraison ---
    delivery_min_fee = models.PositiveIntegerField(
        "Frais livraison minimum (FCFA)",
        default=2000,
        help_text="Montant minimal facturé pour une livraison, si il y a au moins une prestation."
    )
    delivery_price_per_km = models.DecimalField(
        "Prix par km (AR) (FCFA)",
        max_digits=7,
        decimal_places=2,
        default=150.00,
        help_text="Montant par km aller-retour pour calculer la livraison."
    )
    delivery_fixed_fee = models.PositiveIntegerField(
        "Frais fixes livraison (FCFA)",
        default=0,
        help_text="Surcoût fixe ajouté aux frais de livraison (ex : péage, zone, plateforme…)."
    )
    delivery_free_threshold = models.PositiveIntegerField(
        "Seuil de livraison offerte (FCFA)",
        default=0,
        help_text="Au-dessus de ce total TTC client, la livraison peut être offerte (0 = désactivé)."
    )

    # --- Supplément express ---
    EXPRESS_TARGET_CHOICES = [
        ("LAUNDRY", "Blanchisserie"),
        ("FAGNI", "FAGNI"),
        ("DRIVER", "Livreur"),
        ("SPLIT", "Répartition (pour plus tard)"),
    ]

    express_extra_fixed = models.PositiveIntegerField(
        "Supplément express fixe (FCFA)",
        default=0,
        help_text="Montant fixe ajouté pour une livraison express."
    )
    express_extra_percent = models.DecimalField(
        "Supplément express (%)",
        max_digits=5,
        decimal_places=2,
        default=0.00,
        help_text="Pourcentage sur le sous-total prestations pour l'express (0 = désactivé)."
    )
    express_extra_target = models.CharField(
        "Bénéficiaire du supplément express",
        max_length=16,
        choices=EXPRESS_TARGET_CHOICES,
        default="LAUNDRY",
        help_text="Où va le supplément express (par défaut : blanchisserie)."
    )
    express_affects_customer_price = models.BooleanField(
        "Répercuter le supplément express sur le client",
        default=True,
        help_text="Si décoché, le supplément est pris par FAGNI sans augmenter le TTC client."
    )

    # --- Répartition logistique ---
    driver_share_ratio = models.DecimalField(
        "Part livreur sur la livraison (%)",
        max_digits=5,
        decimal_places=2,
        default=70.00,
        help_text="Pourcentage des frais de livraison (hors service FAGNI) reversé au livreur."
    )
    driver_min_fee = models.PositiveIntegerField(
        "Minimum par course pour le livreur (FCFA)",
        default=1000,
        help_text="Plancher de rémunération livreur par commande livrée."
    )
    logistic_margin_ratio = models.DecimalField(
        "Marge logistique FAGNI sur la livraison (%)",
        max_digits=5,
        decimal_places=2,
        default=30.00,
        help_text="Part des frais de livraison gardée par FAGNI (en pratique 100 - part livreur)."
    )

    # --- TVA / Fisc ---
    vat_rate = models.DecimalField(
        "TVA (%)",
        max_digits=5,
        decimal_places=2,
        default=18.00,
        help_text="Taux de TVA appliqué sur les revenus FAGNI."
    )
    # Paramètres FAGNI pilote
    prix_article_fcfa = models.PositiveIntegerField(
        default=500,
        verbose_name="Prix par article (FCFA)",
        help_text="Prix unitaire facturé au client par article"
    )
    driver_amount_per_leg = models.PositiveIntegerField(
        default=800,
        verbose_name="Rémunération livreur par tronçon (FCFA)",
        help_text="Montant payé au livreur pour chaque collecte ou livraison"
    )
    wave_business_number = models.CharField(
        max_length=20,
        default="0748643892",
        verbose_name="Numéro Wave Business",
        help_text="Numéro Wave sur lequel les clients paient"
    )
    whatsapp_ops_number = models.CharField(
        max_length=20,
        default="0142299949",
        verbose_name="Numéro WhatsApp OPS",
        help_text="Numéro WhatsApp du coordinateur OPS"
    )

    apply_vat_on_service_only = models.BooleanField(
        "Appliquer la TVA uniquement sur les revenus FAGNI",
        default=True,
        help_text="Si décoché, la TVA pourra être calculée sur d'autres composantes plus tard."
    )

    def __str__(self):
        return "Paramètres globaux de pricing FAGNI"

    class Meta:
        verbose_name = "Paramètres globaux de pricing"
        verbose_name_plural = "Paramètres globaux de pricing"


# ============================================================
#  B. PARAMÈTRES WORKFLOW / TIMINGS
# ============================================================

class WorkflowSettings(SingletonModel):
    """
    Paramètres de workflow : modes, SLA, enchaînement des statuts, etc.
    """

    PICKUP_MODE_CHOICES = [
        ("immediate", "Immédiate (dès que possible)"),
        ("scheduled", "Programmée"),
    ]

    default_pickup_mode = models.CharField(
        "Mode de collecte par défaut",
        max_length=16,
        choices=PICKUP_MODE_CHOICES,
        default="immediate",
    )

    # SLA
    standard_sla_hours = models.PositiveIntegerField(
        "SLA standard (heures)",
        default=48,
        help_text="Délai cible en heures entre collecte et livraison en mode standard."
    )
    express_sla_hours = models.PositiveIntegerField(
        "SLA express (heures)",
        default=24,
        help_text="Délai cible en heures pour l'express (déjà utilisé dans le front)."
    )
    max_sla_days = models.PositiveIntegerField(
        "Délai max en jours",
        default=7,
        help_text="Au-delà de ce délai, on considère que la livraison est 'programmée long terme'."
    )

    # Règles d'enchaînement
    pickup_cutoff_hour = models.PositiveIntegerField(
        "Heure limite collecte pour express (h)",
        default=10,
        help_text="Ex : avant 10h, on peut encore garantir l'express le lendemain."
    )
    same_day_delivery_allowed = models.BooleanField(
        "Autoriser la livraison le jour même",
        default=False,
        help_text="Si activé, permet des flux ultra-rapides dans certaines zones."
    )

    require_pickup_done_before_delivery_leg_start = models.BooleanField(
        "Exiger la collecte avant de démarrer la livraison",
        default=True,
        help_text="Empêche un livreur de démarrer un tronçon retour si l'aller n'est pas terminé."
    )
    require_laundry_validation_before_delivery_assignment = models.BooleanField(
        "Exiger validation blanchisserie avant l'assignation livraison",
        default=True,
        help_text="Si activé, la jambe 'blanchisserie → client' n'est assignée/démarrable "
                  "qu'après validation du blanchisseur."
    )

    # Annulations
    allow_cancel_after_pickup = models.BooleanField(
        "Autoriser une annulation après collecte",
        default=False,
        help_text="Si vrai, il faudra éventuellement gérer des pénalités."
    )
    cancel_penalty_customer_percent = models.DecimalField(
        "Pénalité client en cas d'annulation (%)",
        max_digits=5,
        decimal_places=2,
        default=0.00,
        help_text="Pourcentage du montant à conserver si le client annule tardivement."
    )
    cancel_penalty_driver_percent = models.DecimalField(
        "Pénalité livreur en cas de no-show (%)",
        max_digits=5,
        decimal_places=2,
        default=0.00,
        help_text="Pourcentage potentiellement retenu sur le livreur (pour futur)."
    )

    def __str__(self):
        return "Paramètres de workflow FAGNI"

    class Meta:
        verbose_name = "Paramètres de workflow"
        verbose_name_plural = "Paramètres de workflow"


# ============================================================
#  C. PARAMÈTRES D'AFFECTATION (ASSIGNMENT)
# ============================================================

class AssignmentSettings(SingletonModel):
    """
    Paramètres pour l'affectation des livreurs et blanchisseries.
    """

    DRIVER_ASSIGNMENT_CHOICES = [
        ("auto", "Automatique"),
        ("manual", "Manuelle"),
        ("hybrid", "Hybride"),
    ]
    LAUNDRY_SELECTION_CHOICES = [
        ("closest", "Blanchisserie la plus proche"),
        ("priority", "Blanchisserie prioritaire"),
        ("manual", "Sélection manuelle"),
    ]

    # Livreurs
    driver_assignment_mode = models.CharField(
        "Mode d'assignation livreur",
        max_length=16,
        choices=DRIVER_ASSIGNMENT_CHOICES,
        default="auto",
    )
    max_concurrent_orders_per_driver = models.PositiveIntegerField(
        "Max de commandes simultanées par livreur",
        default=5,
    )
    driver_radius_km = models.DecimalField(
        "Rayon max pour assigner un livreur (km)",
        max_digits=6,
        decimal_places=2,
        default=15.00,
        help_text="Distance max entre le livreur et le point de collecte/blanchisserie."
    )
    prefer_same_driver_for_round_trip = models.BooleanField(
        "Privilégier le même livreur pour l'A/R",
        default=True,
    )

    # Blanchisseries
    laundry_selection_mode = models.CharField(
        "Mode de sélection blanchisserie",
        max_length=16,
        choices=LAUNDRY_SELECTION_CHOICES,
        default="closest",
    )
    max_daily_orders_per_laundry = models.PositiveIntegerField(
        "Max de commandes / jour / blanchisserie",
        default=50,
    )
    laundry_priority_strategy = models.CharField(
        "Stratégie de priorité blanchisserie (texte court)",
        max_length=128,
        blank=True,
        help_text="Ex : 'premium_dabord', 'equilibrage_volume', etc. Pour usage ultérieur."
    )

    def __str__(self):
        return "Paramètres d'assignation FAGNI"

    class Meta:
        verbose_name = "Paramètres d'assignation"
        verbose_name_plural = "Paramètres d'assignation"


# ============================================================
#  HELPERS PRATIQUES
# ============================================================

def get_pricing_settings() -> GlobalPricingSettings:
    return GlobalPricingSettings.get_solo()


def get_workflow_settings() -> WorkflowSettings:
    return WorkflowSettings.get_solo()


def get_assignment_settings() -> AssignmentSettings:
    return AssignmentSettings.get_solo()


class InvoiceSettings(models.Model):
    """
    Paramètres d'affichage pour l'en-tête / pied de page des documents (ticket / facture).
    Singleton via SingletonModelAdmin dans l'admin.
    """
    company_name = models.CharField("Nom entreprise", max_length=150, default="FAGNI")
    company_tagline = models.CharField("Slogan", max_length=200, blank=True, null=True)

    header_logo = models.ImageField(
        "Logo (en-tête)",
        upload_to="invoice/logo/",
        blank=True,
        null=True,
    )

    company_address = models.CharField("Adresse", max_length=255, blank=True, null=True)
    company_phone = models.CharField("Téléphone", max_length=50, blank=True, null=True)
    company_email = models.EmailField("Email", blank=True, null=True)

    header_note = models.CharField("Note en-tête", max_length=255, blank=True, null=True)
    footer_text = models.TextField("Texte pied de page", blank=True, null=True)

    created_at = models.DateTimeField("Créée le", auto_now_add=True)
    updated_at = models.DateTimeField("Mise à jour le", auto_now=True)

    class Meta:
        verbose_name = "Paramètres facture (en-tête/pied)"
        verbose_name_plural = "Paramètres facture (en-tête/pied)"

    def __str__(self):
        return "Paramètres facture (en-tête/pied)"
