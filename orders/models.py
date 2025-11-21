from decimal import Decimal, ROUND_HALF_UP
import uuid
import math

from django.db import models
from django.db.models import Sum
from django.conf import settings

from partners.models import LaundryPartner, DeliveryPartner, RelayPointPartner
from django.utils import timezone


# --- Paramètres historiques (plus vraiment utilisés, gardés si besoin) ---
# MIN_DELIVERY_FEE = Decimal("2000.00")   # minimum 2000 FCFA
# PRICE_PER_KM = Decimal("200.00")        # 200 FCFA / km
# ROUNDTRIP_FACTOR = Decimal("2.0")       # aller + retour


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
    name = models.CharField("Nom", max_length=120)
    phone = models.CharField("Téléphone", max_length=30, blank=True)
    email = models.EmailField("Email", blank=True)
    address = models.CharField("Adresse", max_length=255, blank=True)

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

    # Identifiant lisible
    code = models.CharField("Code", max_length=20, unique=True, blank=True)

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

    # 🔹 NOUVEAU : point relais partenaire
    relay_partner = models.ForeignKey(
        RelayPointPartner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Point relais partenaire",
    )

    status = models.CharField(
        "Statut",
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    created_at = models.DateTimeField("Créée le", auto_now_add=True)
    updated_at = models.DateTimeField("Mise à jour le", auto_now=True)

    # Montants
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

    # Timestamps opérationnels
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

    class Meta:
        verbose_name = "Commande"
        verbose_name_plural = "Commandes"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code or 'SANS-CODE'} - {self.customer}"

    # ---------- Propriétés de calcul ----------
    @property
    def total_ht(self):
        """
        Somme des lignes (quantité x PU) SANS TVA.
        """
        total = sum((li.line_total for li in self.items.all()), Decimal("0.00"))
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def tva_amount(self):
        """
        TVA (actuellement 0%). Tu pourras mettre 0.18 pour 18%.
        """
        tva_rate = Decimal("0.00")
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

    @property
    def grand_total(self):
        """
        Total global facturé au client :
        - total_ttc (prestations)
        + service_fee FAGNI
        + delivery_fee (transport)
        """
        return (
            self.total_ttc
            + (self.service_fee or Decimal("0.00"))
            + (self.delivery_fee or Decimal("0.00"))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ---------- BASE PRICING (MODELE 3) ----------
    def compute_delivery_pricing(self, one_way_km: Decimal):
        """
        Calcul "BASE" avant majoration dynamique (surge) :

        - distance_totale_km = aller-retour (client <-> blanchisserie)
        - driver_cost_base = ce que FAGNI doit payer au livreur
        - client_fee_base = ce que FAGNI facture au client AVANT surge
        - margin_base = marge FAGNI (client_fee_base - driver_cost_base)

        La logique :
        1) On calcule le coût livreur (km + fixes par jambe)
        2) On calcule une marge cible FAGNI (par km + min)
        3) Prix client = coût livreur + marge cible
        4) On applique les bornes client_min_fee / client_max_fee
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

    # petit alias pour tes tests interactifs avec le "_" et les flags
    def _compute_delivery_pricing(self, one_way_km, is_peak=False, is_night=False, is_rain=False):
        """
        Wrapper pour compatibilité avec les tests shell :
        on ignore pour l'instant is_peak / is_night / is_rain,
        la partie dynamique est gérée dans compute_delivery_fee().
        """
        return self.compute_delivery_pricing(one_way_km)

    def compute_delivery_fee(self, context: dict | None = None):
        """
        V2 dynamique (inspiration Yango / Uber) :

        1) On calcule la distance aller simple (Haversine) client ↔ blanchisserie.
        2) On calcule la tarification BASE (compute_delivery_pricing).
        3) On applique un multiplicateur dynamique (surge) :
           - heures de pointe (7–10, 17–20)
           - nuit (20–6)
           - météo (si fournie dans `context["weather"]`)
        4) On répartit la MAJORATION entre :
           - le livreur (driver_surge_share, ex : 60%)
           - FAGNI (reste)

        Les champs mis à jour :
        - distance_km
        - driver_logistic_cost
        - logistic_margin
        - (delivery_fee est retourné, puis stocké en vue)
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

        # 4.a Heure de la commande / ou maintenant si pas de date
        now = timezone.localtime(self.created_at or timezone.now())
        hour = now.hour

        # Heures de pointe (approx) : 7–10 et 17–20
        peak_multiplier = Decimal(str(logi.get("peak_multiplier", 1.3)))
        night_multiplier = Decimal(str(logi.get("night_multiplier", 1.4)))

        is_peak = (7 <= hour < 10) or (17 <= hour < 20)
        is_night = (hour >= 20) or (hour < 6)

        if is_peak:
            surge_factor *= peak_multiplier
        elif is_night:
            surge_factor *= night_multiplier

        # 4.b Météo (si fournie dans context ou si un jour on ajoute un champ Order.weather)
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
            # part pour le livreur
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

    # ---------- Montants payés / dus (placeholder pour plus tard) ----------
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

    # ---------- Recalcul centralisé ----------
    def save(self, *args, **kwargs):
        """
        On laisse la vue gérer distance_km / delivery_fee / driver_logistic_cost.
        Ici on recalcule seulement :
        - code
        - total (somme des lignes)
        - service_fee
        """
        if not self.code:
            self.code = str(uuid.uuid4())[:8]

        super().save(*args, **kwargs)

        agg = self.items.aggregate(s=Sum("total"))
        total = (agg["s"] or Decimal("0.00")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        fee_service = self.compute_service_fee()

        type(self).objects.filter(pk=self.pk).update(
            total=total,
            service_fee=fee_service,
        )

        # Si tu as le module MLM, on garde la logique :
        if hasattr(self, "mlm_commissions") and self.status == "done" and not self.mlm_commissions.exists():
            self.distribute_mlm_commissions()

    # ---------- MLM ----------
    def distribute_mlm_commissions(self):
        """
        Distribue les commissions MLM sur 3 niveaux à partir du service_fee :
        - N1 = 5%
        - N2 = 3%
        - N3 = 1%
        """
        from mlm.models import ReferralLink, ReferralCommission, WalletTransaction

        try:
            link = ReferralLink.objects.get(customer=self.customer)
        except ReferralLink.DoesNotExist:
            return

        upline = link.get_upline(levels=3)
        if not upline:
            return

        fee_base_decimal = self.service_fee or Decimal("0.00")
        fee_base = int(fee_base_decimal)

        percent_levels = [
            Decimal("5.00"),
            Decimal("3.00"),
            Decimal("1.00"),
        ]

        for idx, sponsor_link in enumerate(upline):
            if idx >= len(percent_levels):
                break

            percent = percent_levels[idx]
            rate = percent / Decimal("100")

            commission_decimal = (fee_base_decimal * rate).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            commission_amount = int(commission_decimal)

            if commission_amount <= 0:
                continue

            level = idx + 1

            ReferralCommission.objects.create(
                beneficiary_profile=sponsor_link,
                level=level,
                order=self,
                service_fee_base=fee_base,
                commission_percent=percent,
                commission_amount=commission_amount,
            )

            WalletTransaction.objects.create(
                profile=sponsor_link,
                type="mlm_commission",
                amount=commission_amount,
                order=self,
                description=f"Commission niveau {level} pour commande {self.code}",
            )


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

    # 🔹 Lien vers la prestation du catalogue (facultatif)
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
        verbose_name="Ligne de commande"
    )
    image = models.ImageField(
        "Photo",
        upload_to="order_items/photos/"
    )
    caption = models.CharField(
        "Description",
        max_length=255,
        blank=True
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
        verbose_name="Catégorie"
    )
    name = models.CharField("Nom", max_length=150)
    code = models.CharField("Code interne", max_length=50, blank=True)
    default_price = models.DecimalField(
        "Prix de base (FCFA)",
        max_digits=10,
        decimal_places=2
    )
    is_active = models.BooleanField("Actif", default=True)

    class Meta:
        verbose_name = "Prestation / Article"
        verbose_name_plural = "Prestations / Articles"
        ordering = ["category", "name"]

    def __str__(self):
        return f"{self.name} ({self.category})"
