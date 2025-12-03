from decimal import Decimal, ROUND_HALF_UP
import uuid
import math

from django.db import models
from django.db.models import Sum
from django.conf import settings
from django.utils import timezone

from partners.models import LaundryPartner, DeliveryPartner, RelayPointPartner



# =====================
#  UTILITAIRE DISTANCE
# =====================
def haversine_distance_km(origin_lat, origin_lng, dest_lat, dest_lng):
    """
    Distance géodésique (en km) entre deux points (lat/lng) avec la formule de Haversine.
    Aucun appel à Google : fonctionne même sans internet ni clé API.
    Retourne un Decimal (2 décimales) ou None si impossible.
    """
    try:
        if origin_lat is None or origin_lng is None or dest_lat is None or dest_lng is None:
            return None

        lat1 = float(origin_lat)
        lon1 = float(origin_lng)
        lat2 = float(dest_lat)
        lon2 = float(dest_lng)
    except (TypeError, ValueError):
        return None

    # Rayon moyen de la Terre en km
    R = 6371.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    km = R * c
    # On repasse en Decimal avec 2 décimales
    return Decimal(str(km)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# =====================
#  CLIENT
# =====================
class Customer(models.Model):
    name = models.CharField(max_length=150, verbose_name="Nom complet")
    phone = models.CharField(max_length=20, verbose_name="Téléphone")
    address = models.CharField(max_length=255, blank=True, verbose_name="Adresse")

    latitude = models.DecimalField(
        "Latitude",
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    longitude = models.DecimalField(
        "Longitude",
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Client"
        verbose_name_plural = "Clients"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.phone})" if self.phone else self.name


# =====================
#  COMMANDE
# =====================
class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "En attente"),
        ("in_progress", "En cours"),
        ("done", "Terminée"),
        ("canceled", "Annulée"),
    ]

    # --------- Statut ---------
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        verbose_name="Statut",
    )

    # 🔹 Collecte programmée (option)
    scheduled_pickup_at = models.DateTimeField(
        "Date/heure de collecte programmée",
        null=True,
        blank=True,
        help_text="Si renseigné, la collecte est prévue à cette date/heure.",
    )

    # Identifiant lisible
    code = models.CharField("Code", max_length=20, unique=True, blank=True)

    # --------- Liens principaux ---------
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="Client",
    )

    laundry_partner = models.ForeignKey(
        LaundryPartner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Blanchisserie partenaire",
    )

    delivery_partner = models.ForeignKey(
        DeliveryPartner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Livreur partenaire",
    )

    # Point relais partenaire (optionnel)
    relay_partner = models.ForeignKey(
        RelayPointPartner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Point relais partenaire",
    )

    created_at = models.DateTimeField("Créée le", auto_now_add=True)
    updated_at = models.DateTimeField("Mise à jour le", auto_now=True)

    notes = models.TextField(
        "Notes / instructions internes",
        blank=True,
        null=True,
        help_text="Notes internes sur la commande (consignes, contexte, etc.)",
    )

    # --------- Parrainage / MLM ---------
    referral_code = models.CharField(
        "Code parrain saisi",
        max_length=50,
        blank=True,
        null=True,
        help_text="Code parrain saisi lors de la création de la commande.",
    )

    # --------- Montants ---------
    total = models.DecimalField(
        "Total prestations TTC",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    # Montant du service FAGNI (5% min 500 FCFA)
    service_fee = models.DecimalField(
        "Service FAGNI (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    # Distance réelle totale (aller + retour) en km
    distance_km = models.DecimalField(
        "Distance totale (km)",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )

    # Frais de livraison facturés au client
    delivery_fee = models.DecimalField(
        "Frais de livraison (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    # Coût logistique interne (pour FAGNI / livreur)
    driver_logistic_cost = models.DecimalField(
        "Coût logistique livreur (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    # Marge logistique FAGNI (livraison)
    logistic_margin = models.IntegerField(
        "Marge logistique FAGNI",
        default=0,
    )

    # --------- Timestamps opérationnels ---------
    pickup_time = models.DateTimeField(
        "Collectée par le livreur",
        null=True,
        blank=True,
    )
    dropoff_time = models.DateTimeField(
        "Déposée à la blanchisserie",
        null=True,
        blank=True,
    )
    wash_complete_time = models.DateTimeField(
        "Fin de lavage",
        null=True,
        blank=True,
    )
    return_time = models.DateTimeField(
        "Reprise par le livreur",
        null=True,
        blank=True,
    )
    delivered_time = models.DateTimeField(
        "Livrée au client",
        null=True,
        blank=True,
    )

    mlm_distributed = models.BooleanField(
        "Commissions MLM déjà distribuées",
        default=False,
    )

    class Meta:
        verbose_name = "Commande"
        verbose_name_plural = "Commandes"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code or 'SANS-CODE'} - {self.customer}"


    def compute_totals(self, save: bool = True):
        """
        Recalcule les montants de la commande à partir des lignes :

        - total = somme des OrderItem.total
        - service_fee = 5 % du total, minimum 500 FCFA si total > 0
        - delivery_fee : laissé tel quel (ou 0 si None)
        - vat_amount = TVA 18 % sur (total + service_fee + delivery_fee)
        - grand_total = base_ht + vat_amount

        Si save=True, on persiste total / service_fee / delivery_fee en base.
        vat_amount et grand_total restent des attributs "en mémoire".
        """
        from django.db.models import Sum

        # 1) Sous-total prestations (somme des lignes)
        agg = self.items.aggregate(s=Sum("total"))
        total_ht = (agg["s"] or Decimal("0.00")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        # On stocke ce sous-total dans le champ total
        self.total = total_ht

        # 2) Service FAGNI : 5 % min 500 FCFA si total > 0
        service_fee = Decimal("0.00")
        if total_ht > 0:
            service_fee = (total_ht * Decimal("0.05")).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            if service_fee < Decimal("500.00"):
                service_fee = Decimal("500.00")
        self.service_fee = service_fee

        # 3) Frais de livraison : on garde ce qui est en base, sinon 0
        if self.delivery_fee is None:
            self.delivery_fee = Decimal("0.00")
        else:
            self.delivery_fee = Decimal(str(self.delivery_fee)).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )

        # 4) Base HT : total prestations + service FAGNI + livraison
        base_ht = (
            (self.total or Decimal("0.00"))
            + (self.service_fee or Decimal("0.00"))
            + (self.delivery_fee or Decimal("0.00"))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 5) TVA 18 %
        vat_rate = Decimal("0.18")
        self.vat_amount = (base_ht * vat_rate).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        # 6) Total TTC
        self.grand_total = (base_ht + self.vat_amount).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        # 7) Sauvegarde des champs persistés si demandé
        if save and self.pk:
            type(self).objects.filter(pk=self.pk).update(
                total=self.total,
                service_fee=self.service_fee,
                delivery_fee=self.delivery_fee,
            )

    # ---------- Propriétés de calcul ----------
    @property
    def total_ht(self):
        """
        Somme des lignes (quantité x PU) SANS TVA.
        Ici on utilise OrderItem.total (déjà qty x PU) comme base HT.
        """
        total = sum((item.total for item in self.items.all()), Decimal("0.00"))
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def tva_amount(self):
        """
        TVA (actuellement 0%). Tu pourras mettre 0.18 pour 18%.
        """
        tva_rate = Decimal("0.18")
        return (self.total_ht * tva_rate).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    @property
    def total_ttc(self):
        """
        Total TTC des prestations (hors service FAGNI et hors transport).
        """
        return (self.total_ht + self.tva_amount).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    # ---------- BASE PRICING (MODELE 3) ----------
    def compute_delivery_pricing(self, one_way_km: Decimal):
        """
        Calcul "BASE" avant majoration dynamique (surge).

        Retourne :
        - distance_totale (km)
        - client_fee_base
        - driver_cost_base
        - margin_base
        """
        if one_way_km is None:
            return None, Decimal("0.00"), Decimal("0.00"), Decimal("0.00")

        if not isinstance(one_way_km, Decimal):
            one_way_km = Decimal(str(one_way_km))

        one_way_km = one_way_km.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        distance_totale = (one_way_km * Decimal("2.0")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        logi = getattr(settings, "FAGNI_LOGISTICS", {})

        # --- Paramètres économiques ---
        client_min_fee = Decimal(str(logi.get("client_min_fee", 1000)))
        client_max_fee = Decimal(str(logi.get("client_max_fee", 5000)))
        client_price_per_km = Decimal(str(logi.get("client_price_per_km", 100)))
        client_fixed_fee = Decimal(str(logi.get("client_fixed_fee", 300)))

        driver_price_per_km = Decimal(str(logi.get("driver_price_per_km", 75)))
        driver_fixed_per_leg = Decimal(str(logi.get("driver_fixed_per_leg", 300)))

        fagni_margin_per_km = Decimal(str(logi.get("fagni_margin_per_km", 100)))
        fagni_min_margin = Decimal(str(logi.get("fagni_min_margin", 300)))

        # 1) Coût livreur de base (km AR + 2 jambes)
        driver_cost_base = (
            distance_totale * driver_price_per_km
            + Decimal("2.0") * driver_fixed_per_leg
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 2) Marge cible FAGNI
        target_margin = (
            distance_totale * fagni_margin_per_km
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if target_margin < fagni_min_margin:
            target_margin = fagni_min_margin

        # 3) Prix client théorique = coût livreur + marge cible
        client_fee_raw = (driver_cost_base + target_margin).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # 4) Application des bornes min / max client
        client_fee_base = client_fee_raw
        if client_fee_base < client_min_fee:
            client_fee_base = client_min_fee
        if client_max_fee > 0 and client_fee_base > client_max_fee:
            client_fee_base = client_max_fee

        margin_base = (client_fee_base - driver_cost_base).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        return distance_totale, client_fee_base, driver_cost_base, margin_base

    # petit alias pour tests interactifs
    def _compute_delivery_pricing(self, one_way_km, is_peak=False, is_night=False, is_rain=False):
        return self.compute_delivery_pricing(one_way_km)

    def compute_delivery_fee(self, context: dict | None = None):
        """
        V2 dynamique (inspiration Yango / Uber).
        Met à jour :
        - distance_km
        - driver_logistic_cost
        - logistic_margin
        et retourne le montant à facturer au client (delivery_fee).
        """
        context = context or {}

        # 1) Il faut un client ET une blanchisserie
        if not self.customer or not self.laundry_partner:
            self.distance_km = None
            self.driver_logistic_cost = Decimal("0.00")
            self.logistic_margin = 0
            return Decimal("0.00")

        origin_lat = getattr(self.customer, "latitude", None)
        origin_lng = getattr(self.customer, "longitude", None)
        dest_lat = getattr(self.laundry_partner, "latitude", None)
        dest_lng = getattr(self.laundry_partner, "longitude", None)

        # Distance aller simple
        one_way_km = haversine_distance_km(origin_lat, origin_lng, dest_lat, dest_lng)

        logi = getattr(settings, "FAGNI_LOGISTICS", {})
        client_min_fee = Decimal(str(logi.get("client_min_fee", 1000)))
        client_max_fee = Decimal(str(logi.get("client_max_fee", 5000)))
        driver_surge_share = Decimal(str(logi.get("driver_surge_share", 0.6)))

        # 2) Si impossible de calculer la distance → on applique le minimum client
        if one_way_km is None:
            self.distance_km = None
            self.driver_logistic_cost = Decimal("0.00")
            self.logistic_margin = int(client_min_fee)
            return client_min_fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 3) Base pricing (sans surge)
        (
            distance_totale,
            client_fee_base,
            driver_cost_base,
            margin_base,
        ) = self.compute_delivery_pricing(one_way_km)

        # 4) Calcul du multiplicateur dynamique (surge_factor)
        surge_factor = Decimal("1.00")

        now = timezone.localtime(self.created_at or timezone.now())
        hour = now.hour

        peak_multiplier = Decimal(str(logi.get("peak_multiplier", 1.3)))
        night_multiplier = Decimal(str(logi.get("night_multiplier", 1.4)))

        is_peak = (7 <= hour < 10) or (17 <= hour < 20)
        is_night = (hour >= 20) or (hour < 6)

        if is_peak:
            surge_factor *= peak_multiplier
        elif is_night:
            surge_factor *= night_multiplier

        weather = context.get("weather", "clear")  # "clear", "rain", "heavy_rain"
        rain_multiplier = Decimal(str(logi.get("rain_multiplier", 1.3)))
        heavy_rain_multiplier = Decimal(str(logi.get("heavy_rain_multiplier", 1.6)))

        if weather == "rain":
            surge_factor *= rain_multiplier
        elif weather == "heavy_rain":
            surge_factor *= heavy_rain_multiplier

        # 5) Nouveau prix client après surge (borné min / max)
        client_fee_surge = (client_fee_base * surge_factor).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        if client_fee_surge < client_min_fee:
            client_fee_surge = client_min_fee
        if client_max_fee > 0 and client_fee_surge > client_max_fee:
            client_fee_surge = client_max_fee

        # 6) Répartition de la MAJORATION entre livreur et FAGNI
        extra = (client_fee_surge - client_fee_base).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        if extra > 0:
            extra_driver = (extra * driver_surge_share).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            extra_driver = Decimal("0.00")

        driver_cost_final = (driver_cost_base + extra_driver).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        margin_final = (client_fee_surge - driver_cost_final).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # 7) Mise à jour des champs modèle
        self.distance_km = distance_totale
        self.driver_logistic_cost = driver_cost_final
        self.logistic_margin = int(margin_final)

        return client_fee_surge

    @property
    def total_photos(self):
        """
        Nombre total de photos sur toutes les lignes de la commande.
        """
        return sum((item.photos.count() for item in self.items.all()), 0)

    def get_all_photos(self):
        """
        Retourne une liste de TOUTES les photos
        rattachées aux lignes de cette commande.
        Utilisé pour la galerie globale dans detail.html.
        """
        photos = []
        qs = self.items.prefetch_related("photos").all()
        for item in qs:
            for p in item.photos.all():
                photos.append(p)
        return photos

    # ---------- Service fee FAGNI ----------
    def compute_service_fee(self):
        """
        Service fee FAGNI :
        - 5% du total_ht
        - minimum = 500 FCFA
        """
        ht = self.total_ht
        if ht <= 0:
            return Decimal("0.00")

        base = (ht * Decimal("0.05")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        minimum = Decimal("500.00")
        fee = base if base >= minimum else minimum
        return fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ---------- Montants payés / dus ----------
    @property
    def amount_paid(self):
        """
        Somme des paiements 'paid' (quand on branchera le module paiements).
        """
        if not hasattr(self, "payments"):
            return Decimal("0.00")
        paid = sum(
            (p.amount for p in self.payments.filter(status="paid")),
            Decimal("0.00"),
        )
        return paid.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def amount_due(self):
        return (self.total_ttc - self.amount_paid).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )


    @property
    def distribute_mlm_commissions(self):
        """
        Distribue les commissions MLM sur 3 niveaux à partir du service_fee :

        - N1 = n1_percent (MLMSettings)
        - N2 = n2_percent
        - N3 = n3_percent

        Les paramètres (pourcentages, seuils, activation) sont pilotés
        par le modèle MLMSettings (configuration MLM active).

        ⚠️ Cette propriété a des effets de bord :
        elle crée des ReferralCommission ET des WalletTransaction (wallets app).
        À appeler UNE SEULE FOIS au bon moment (ex : commande payée / livrée).
        """
        from mlm.models import ReferralLink, ReferralCommission, MLMSettings
        from wallets.models import Wallet, WalletTransaction

        # 🔒 Sécurité 1 : commande déjà marquée comme distribuée
        if self.mlm_distributed:
            return

        # 🔒 Sécurité 2 : commissions déjà enregistrées pour cette commande
        if ReferralCommission.objects.filter(order=self).exists():
            if not self.mlm_distributed and self.pk:
                type(self).objects.filter(pk=self.pk).update(mlm_distributed=True)
                self.mlm_distributed = True
            return

        # 0) Charger la config MLM active
        mlm_cfg = MLMSettings.get_active()
        if not mlm_cfg.enabled:
            return

        # 1) Récupérer le profil MLM du client
        try:
            link = ReferralLink.objects.get(customer=self.customer)
        except ReferralLink.DoesNotExist:
            return

        # 2) Remonter la lignée jusqu’à 3 niveaux
        upline = link.get_upline(levels=3)
        if not upline:
            return

        # 3) Base de calcul = service_fee de la commande
        fee_base_decimal = self.service_fee or Decimal("0.00")
        if fee_base_decimal <= 0:
            return

        fee_base_decimal = fee_base_decimal.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        # Seuil minimum pour générer des commissions
        min_fee = Decimal(str(mlm_cfg.min_service_fee_for_commission or 0))
        if fee_base_decimal < min_fee:
            return

        fee_base_int = int(fee_base_decimal)

        # 4) Pourcentages par niveau issus de la config active
        percent_levels = [
            mlm_cfg.n1_percent or Decimal("0.00"),
            mlm_cfg.n2_percent or Decimal("0.00"),
            mlm_cfg.n3_percent or Decimal("0.00"),
        ]

        # 5) Boucle sur les parrains de la lignée
        for idx, sponsor_link in enumerate(upline):
            if idx >= len(percent_levels):
                break  # sécurité

            sponsor_customer = getattr(sponsor_link, "customer", None)
            if sponsor_customer is None:
                continue

            percent = percent_levels[idx] or Decimal("0.00")
            if percent <= 0:
                continue

            rate = percent / Decimal("100")

            # Commission théorique
            commission_decimal = (fee_base_decimal * rate).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            commission_int = int(commission_decimal)

            if commission_int <= 0:
                continue

            level = idx + 1  # N1, N2, N3...

            # 5.a Enregistrer la commission détaillée MLM
            ReferralCommission.objects.create(
                beneficiary_profile=sponsor_link,
                level=level,
                order=self,
                service_fee_base=fee_base_int,
                commission_percent=percent,
                commission_amount=commission_int,
            )

            # 5.b Crédite le wallet client du parrain (Wallet FAGNI)
            wallet, _ = Wallet.objects.get_or_create(
                owner_type="customer",
                customer=sponsor_customer,
                defaults={"currency": "XOF"},
            )

            commission_amount_decimal = Decimal(commission_int).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )

            # 🔹 Met à jour le solde du wallet
            wallet.balance = (wallet.balance + commission_amount_decimal).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            wallet.save(update_fields=["balance", "updated_at"])

            # 🔹 Trace la transaction MLM
            WalletTransaction.objects.create(
                wallet=wallet,
                order=self,
                type="mlm_commission",
                direction="in",
                amount=commission_amount_decimal,
                description=f"Commission niveau {level} pour commande {self.code}",
            )

        # 6) Marquer la commande comme distribuée (une seule fois)
        if self.pk:
            type(self).objects.filter(pk=self.pk).update(mlm_distributed=True)
        self.mlm_distributed = True


# =====================
#  JAMBES DE LIVRAISON
# =====================
class DeliveryLeg(models.Model):
    """
    Une "jambe" de livraison pour une commande FAGNI.

    Exemples :
    - leg_type = "pickup" : Client -> Blanchisserie
    - leg_type = "return" : Blanchisserie -> Client

    But :
    - Permettre que la collecte et la livraison finale
      soient réalisées par des livreurs différents.
    - Pouvoir suivre les montants (part livreur / part FAGNI)
      par jambe.
    """

    LEG_TYPE_CHOICES = [
        ("pickup", "Client → Blanchisserie"),
        ("return", "Blanchisserie → Client"),
    ]

    STATUS_CHOICES = [
        ("pending", "En attente"),
        ("assigned", "Assignée"),
        ("in_progress", "En cours"),
        ("done", "Terminée"),
        ("canceled", "Annulée"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="legs",
        verbose_name="Commande",
    )

    driver = models.ForeignKey(
        DeliveryPartner,
        on_delete=models.PROTECT,
        related_name="delivery_legs",
        verbose_name="Livreur partenaire",
    )

    leg_type = models.CharField(
        "Type de jambe",
        max_length=20,
        choices=LEG_TYPE_CHOICES,
    )

    status = models.CharField(
        "Statut",
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    # Distance pour cette jambe (souvent l'aller simple)
    distance_km = models.DecimalField(
        "Distance (km)",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )

    # Part du prix de livraison rattachée à cette jambe
    client_fee_share = models.DecimalField(
        "Part client (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    # Montant versé au livreur pour cette jambe
    driver_amount = models.DecimalField(
        "Montant livreur (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    # Marge FAGNI sur cette jambe
    fagni_margin = models.DecimalField(
        "Marge FAGNI (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    created_at = models.DateTimeField("Créée le", auto_now_add=True)
    started_at = models.DateTimeField("Début de la course", null=True, blank=True)
    finished_at = models.DateTimeField("Fin de la course", null=True, blank=True)

    class Meta:
        verbose_name = "Jambe de livraison"
        verbose_name_plural = "Jambes de livraison"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_leg_type_display()} - {self.order.code} - {self.driver}"


# =====================
#  LIGNE DE COMMANDE
# =====================
class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Commande",
    )

    # Lien vers la prestation du catalogue (facultatif)
    service = models.ForeignKey(
        "ServiceItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Prestation cataloguée",
    )

    designation = models.CharField("Désignation", max_length=120)
    quantity = models.PositiveIntegerField("Quantité", default=1)

    unit_price = models.DecimalField(
        "Prix unitaire (FCFA)",
        max_digits=10,
        decimal_places=2,
    )

    total = models.DecimalField(
        "Total ligne (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    class Meta:
        verbose_name = "Ligne de commande"
        verbose_name_plural = "Lignes de commande"

    def __str__(self):
        return f"{self.designation} x{self.quantity}"

    @property
    def line_total(self):
        q = Decimal(self.quantity or 0)
        p = self.unit_price or Decimal("0.00")
        return (q * p).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def save(self, *args, **kwargs):
        self.total = self.line_total
        super().save(*args, **kwargs)


# =====================
#  Photos des lignes
# =====================
class OrderItemPhoto(models.Model):
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Ligne de commande",
    )
    image = models.ImageField(
        "Photo",
        upload_to="order_items/photos/",
    )
    caption = models.CharField(
        "Description",
        max_length=255,
        blank=True,
    )
    created_at = models.DateTimeField("Ajoutée le", auto_now_add=True)

    class Meta:
        verbose_name = "Photo d'article"
        verbose_name_plural = "Photos d'articles"

    def __str__(self):
        return f"Photo {self.id} - {self.order_item}"


# =====================
#  Catalogue de services
# =====================
class ServiceCategory(models.Model):
    """
    Exemples : Blanchisserie, Cordonnerie, Retouche, Repassage, etc.
    """
    name = models.CharField("Nom de la catégorie", max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField("Description", blank=True)

    class Meta:
        verbose_name = "Catégorie de service"
        verbose_name_plural = "Catégories de service"
        ordering = ["name"]

    def __str__(self):
        return self.name


class ServiceItem(models.Model):
    """
    Article / prestation du catalogue FAGNI.
    Ex : Chemise lavage simple, Pantalon + repassage, Réparation de talon, etc.
    """
    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.CASCADE,
        related_name="services",
        verbose_name="Catégorie",
    )
    name = models.CharField("Nom", max_length=150)
    code = models.CharField("Code interne", max_length=50, blank=True)
    default_price = models.DecimalField(
        "Prix de base (FCFA)",
        max_digits=10,
        decimal_places=2,
    )
    is_active = models.BooleanField("Actif", default=True)

    class Meta:
        verbose_name = "Prestation / Article"
        verbose_name_plural = "Prestations / Articles"
        ordering = ["category", "name"]

    def __str__(self):
        return f"{self.name} ({self.category})"


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(
        "Order",
        related_name="status_history",
        on_delete=models.CASCADE
    )
    previous_status = models.CharField(max_length=50, blank=True, null=True)
    new_status = models.CharField(max_length=50)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.order.code} : {self.previous_status} → {self.new_status} ({self.changed_at})"
