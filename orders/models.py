from decimal import Decimal, ROUND_HALF_UP
import uuid
import math
from orders.utils.distances import haversine_distance_km
from django.db import models
from django.db.models import Sum, F, DecimalField
from django.conf import settings
from django.utils import timezone
from partners.models import LaundryPartner, DeliveryPartner, RelayPointPartner
from .finance import compute_order_financials
from .services import recompute_order_distance_from_legs


def _round_fcfa(value):
    """
    Petit helper pour arrondir proprement en FCFA (entier).
    """
    if value is None:
        return Decimal("0")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


# ============================
#  SYNC DES JAMBES DE LIVRAISON
# ============================
def sync_delivery_legs_for_order(order):
    """
    Synchronise les DeliveryLeg à partir des infos de la commande.

    - Si pas de delivery_partner ou pas de delivery_fee -> on supprime les jambes.
    - Sinon on crée / met à jour 2 jambes : pickup & return,
      avec répartition du montant client / livreur / FAGNI.

    ✅ FIX CRITIQUE:
    - Si une jambe a déjà été PAYÉE (WalletTransaction payout/in liée à leg),
      alors on NE RÉÉCRIT PAS driver_amount avec un nouveau calcul.
      La tx est la source de vérité.
    - On évite de delete() des legs si possible, pour préserver l'identité (leg_id)
      et garder l'anti-doublon solide.
    """
    from decimal import Decimal
    from django.db.models.deletion import ProtectedError

    delivery_fee = order.delivery_fee or Decimal("0")
    driver_total = order.amount_driver_partner or Decimal("0")
    margin_total = Decimal(order.logistic_margin or 0)

    # distance totale A/R (si renseignée)
    distance_total = order.distance_km or order.distance_km_total or Decimal("0")

    # Helper: reset timestamps logistiques (anti-legacy)
    def _reset_logistic_timestamps():
        # On ne touche PAS wash_complete_time (côté blanchisserie)
        dirty = False
        for field in ("pickup_time", "dropoff_time", "return_time", "delivered_time"):
            if getattr(order, field, None) is not None:
                setattr(order, field, None)
                dirty = True
        return dirty

    # ------------------------------------------------------------
    # 1) Si pas de livreur OU pas de livraison facturée -> supprimer legs
    # ------------------------------------------------------------
    if (not order.delivery_partner) or (delivery_fee <= 0):
        from orders.models import DeliveryLeg  # safe local import
        DeliveryLeg.objects.filter(order=order).delete()

        changed = False
        # ✅ SAFE : ne reset la timeline que si la commande est encore pending
        if order.status == "pending":
            changed = _reset_logistic_timestamps()

        # Statut cohérent : s'il n'y a pas de legs, on revient en pending
        if order.status != "pending":
            order.status = "pending"
            changed = True

        if changed:
            order.save(update_fields=["pickup_time", "dropoff_time", "return_time", "delivered_time", "status"])
        return

    # ------------------------------------------------------------
    # 2) Récupérer legs existants
    # ------------------------------------------------------------
    from orders.models import DeliveryLeg  # safe local import

    legs_qs = DeliveryLeg.objects.filter(order=order).order_by("id")

    # ✅ IMPORTANT : on évite de delete() si on peut, pour préserver leg_id
    # (et donc l'anti-doublon des payouts).
    try:
        legs_existing = list(legs_qs)
        # On tente delete uniquement si aucun leg n'existe (cas rare)
        # ou si tu veux vraiment repartir de zéro (désactivé par défaut).
        # legs_qs.delete()
    except ProtectedError:
        legs_existing = list(legs_qs)

    # ✅ SAFE : reset timeline uniquement si la commande est encore en pending
    changed = False
    if order.status == "pending":
        changed = _reset_logistic_timestamps()

    # Distance aller simple (si on a une distance totale A/R)
    distance_one_way = None
    if distance_total and distance_total > 0:
        if not isinstance(distance_total, Decimal):
            distance_total = Decimal(str(distance_total))
        distance_one_way = (distance_total / Decimal("2")).quantize(Decimal("0.01"))

    # -------- Répartition 50/50 avec correction sur la 2e jambe --------
    def _round_fcfa(value):
        from decimal import ROUND_HALF_UP
        if value is None:
            return Decimal("0")
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    client_share_1 = _round_fcfa(delivery_fee / 2)
    client_share_2 = _round_fcfa(delivery_fee - client_share_1)

    driver_share_1 = _round_fcfa(driver_total / 2)
    driver_share_2 = _round_fcfa(driver_total - driver_share_1)

    margin_share_1 = _round_fcfa(margin_total / 2)
    margin_share_2 = _round_fcfa(margin_total - margin_share_1)

    # Statut de base des jambes
    if order.status == "done":
        base_status = "done"
    elif order.status == "in_progress":
        base_status = "assigned"
    else:
        base_status = "pending"

    legs_data = [
        ("pickup", client_share_1, driver_share_1, margin_share_1),
        ("return", client_share_2, driver_share_2, margin_share_2),
    ]

    # Map des legs existants par type (pickup/return) en gardant le "meilleur"
    _status_rank = {"done": 5, "in_progress": 4, "assigned": 3, "pending": 2, "canceled": 1}

    existing_by_type = {}
    for leg in legs_existing:
        lt = getattr(leg, "leg_type", None)
        if lt not in {"pickup", "return"}:
            continue
        cur = existing_by_type.get(lt)
        rank = _status_rank.get(getattr(leg, "status", None), 0)
        cur_rank = _status_rank.get(getattr(cur, "status", None), 0) if cur else -1
        if (cur is None) or (rank > cur_rank) or (rank == cur_rank and getattr(leg, "id", 0) > getattr(cur, "id", 0)):
            existing_by_type[lt] = leg

    # ✅ Annule les doublons pickup/return qui ne sont pas retenus
    for leg in legs_existing:
        lt = getattr(leg, "leg_type", None)
        if lt in {"pickup", "return"}:
            kept = existing_by_type.get(lt)
            if kept and getattr(leg, "id", None) != getattr(kept, "id", None):
                if getattr(leg, "status", None) != "canceled":
                    leg.status = "canceled"
                    leg.save(update_fields=["status"])

    # ✅ CANCEL legs legacy/protégés
    for leg in legs_existing:
        lt = getattr(leg, "leg_type", None)
        if lt not in {"pickup", "return"}:
            if getattr(leg, "status", None) != "canceled":
                leg.status = "canceled"
                leg.save(update_fields=["status"])

    # ------------------------------------------------------------
    # ✅ RÈGLE BUSINESS : return ne doit pas être "assigné"
    # tant que pickup n'est pas "done"
    # ------------------------------------------------------------
    pickup_leg = existing_by_type.get("pickup")
    pickup_done = bool(pickup_leg and pickup_leg.status == "done")
    base_status_return = base_status if pickup_done else "pending"

    # ------------------------------------------------------------
    # ✅ Helper: détecter si la jambe a déjà un payout (source de vérité)
    # ------------------------------------------------------------
    def _paid_amount_for_leg(leg):
        try:
            from wallets.models import WalletTransaction
            tx = WalletTransaction.objects.filter(
                order=order,
                leg=leg,
                type="payout",
                direction="in",
            ).order_by("id").first()
            return tx.amount if tx else None
        except Exception:
            return None

    # ------------------------------------------------------------
    # 3) CREATE/UPDATE legs (sans rétrograder un leg avancé)
    # ------------------------------------------------------------
    for leg_type, client_part, driver_part, margin_part in legs_data:
        leg = existing_by_type.get(leg_type)

        desired_status = base_status
        if leg_type == "return":
            desired_status = base_status_return

        if leg:
            leg.driver = order.delivery_partner

            # 🔒 NE JAMAIS "reculer" un leg déjà avancé
            if leg.status not in {"done", "canceled", "in_progress"}:
                leg.status = desired_status

            leg.distance_km = distance_one_way
            leg.client_fee_share = client_part
            leg.fagni_margin = margin_part

            # ✅ LOCK driver_amount si déjà payé
            paid_amount = _paid_amount_for_leg(leg)
            if paid_amount is not None:
                leg.driver_amount = paid_amount
            else:
                leg.driver_amount = driver_part

            leg.save(update_fields=[
                "driver", "status", "distance_km",
                "client_fee_share", "driver_amount", "fagni_margin"
            ])

        else:
            # Création d'une jambe neuve (pas encore payée, donc OK)
            DeliveryLeg.objects.create(
                order=order,
                driver=order.delivery_partner,
                leg_type=leg_type,
                status=desired_status,
                distance_km=distance_one_way,
                client_fee_share=client_part,
                driver_amount=driver_part,
                fagni_margin=margin_part,
            )

    # ✅ Après sync legs : aligner le statut de la commande sur les legs
    try:
        from orders.models import sync_order_status_from_legs
        sync_order_status_from_legs(order, save=True)
    except Exception:
        pass


def _order_status_to_leg_status(order_status: str) -> str:
    """
    Mapping du statut commande vers le statut des legs.
    """
    if order_status == "done":
        return "done"
    if order_status == "in_progress":
        return "assigned"
    # pending/canceled -> pending (canceled géré ailleurs)
    return "pending"


def sync_legs_status_from_order(order, save: bool = True) -> int:
    """
    Aligne les legs sur le statut de la commande.
    - pending     => legs pending (sans jamais rétrograder un leg déjà démarré)
    - in_progress => legs assigned (ou laisse in_progress/done/canceled tels quels)
    - done        => legs done
    Retourne nb de legs modifiés.
    """
    from orders.models import DeliveryLeg  # safe local import

    qs = DeliveryLeg.objects.filter(order=order)
    if not qs.exists():
        return 0

    target = _order_status_to_leg_status(getattr(order, "status", "pending") or "pending")
    changed = 0

    for leg in qs:
        st = (leg.status or "").lower()

        # Ne jamais rétrograder les statuts "finalisés" ou déjà démarrés
        if st in ("done", "canceled", "in_progress"):
            continue

        if target == "pending":
            # ✅ IMPORTANT: ne rétrograde pas un leg "assigned" si jamais il a déjà été démarré
            # Ici on ne touche que les legs non démarrés (assigned/pending).
            if st == "assigned":
                leg.status = "pending"
                leg.save(update_fields=["status"])
                changed += 1

        elif target == "assigned":
            # si pending => assigned, sinon on laisse (évite de toucher d'autres statuts)
            if st == "pending":
                leg.status = "assigned"
                leg.save(update_fields=["status"])
                changed += 1

        elif target == "done":
            if st != "done":
                leg.status = "done"
                leg.save(update_fields=["status"])
                changed += 1

    return changed


def sync_order_status_from_legs(order, save=False):
    """
    Synchronise le statut de la commande à partir des DeliveryLeg.

    Règles :
    - 0 leg actif        → pending
    - tous les legs done → done
    - au moins un leg assigned / in_progress / done → in_progress
    """

    from orders.models import DeliveryLeg

    legs = (
        DeliveryLeg.objects
        .filter(order=order)
        .exclude(status="canceled")
    )

    # Aucun leg actif
    if not legs.exists():
        new_status = "pending"

    # Tous les legs terminés
    elif not legs.exclude(status="done").exists():
        new_status = "done"

    else:
        any_started = legs.filter(
            status__in=["assigned", "in_progress", "done"]
        ).exists()

        if any_started:
            new_status = "in_progress"
        else:
            new_status = "pending"

    if getattr(order, "status", None) != new_status:
        order.status = new_status
        if save:
            order.save(update_fields=["status"])

    return new_status


# =====================
#  CLIENT
# =====================
class Customer(models.Model):
    name = models.CharField(max_length=150, verbose_name="Nom complet")
    phone = models.CharField(max_length=20, verbose_name="Téléphone", unique=True)
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
        name = (self.name or "").strip() or "Client"
        phone = (self.phone or "").strip()
        return f"{name} ({phone})" if phone else name


class LogisticsConfig(models.Model):
    """
    Configuration logistique globale / par ville.

    Phase 1 :
    - on stocke les paramètres de base (cutoff, délais, supplément express, créneaux programmés),
    - ils sont utilisés dans les vues (create / détail) pour afficher les infos
      et, plus tard, pour calculer les délais & prix côté serveur.
    """

    name = models.CharField(
        "Nom de la configuration",
        max_length=150,
        default="Configuration par défaut",
    )

    city = models.CharField(
        "Ville / zone (optionnel)",
        max_length=100,
        blank=True,
        null=True,
        help_text="Ex : Abidjan, Bouaké… (facultatif pour l’instant).",
    )

    is_active = models.BooleanField(
        "Configuration active",
        default=True,
        help_text="Une seule config active sera utilisée par défaut.",
    )

    # ----- COLLECTE -----
    pickup_cutoff_hour = models.PositiveSmallIntegerField(
        "Heure limite collecte (cutoff)",
        default=10,
        help_text=(
            "Heure limite (0–23). Si la collecte est effectuée après cette heure, "
            "il sera plus difficile de garantir le 24h/48h."
        ),
    )

    # ----- DÉLAIS (SLA) -----
    standard_sla_hours = models.PositiveIntegerField(
        "Délai standard (heures)",
        default=48,
        help_text="Délai cible (en heures) pour la formule standard (ex : 48h).",
    )
    express_sla_hours = models.PositiveIntegerField(
        "Délai express (heures)",
        default=24,
        help_text="Délai cible (en heures) pour la formule express (ex : 24h).",
    )

    # ----- EXPRESS (supplément) -----
    express_enabled = models.BooleanField(
        "Activer la livraison express",
        default=True,
    )

    express_extra_flat = models.DecimalField(
        "Supplément express (montant fixe, FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Ex : 1000 FCFA de plus pour une livraison express.",
    )

    express_extra_percent = models.DecimalField(
        "Supplément express (% du total prestations + service FAGNI)",
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Ex : 10 = 10% du montant de base. Peut être 0.",
    )

    # ----- LIVRAISON PROGRAMMÉE -----
    scheduled_slots_enabled = models.BooleanField(
        "Activer la livraison programmée",
        default=True,
    )
    scheduled_window_start_hour = models.PositiveSmallIntegerField(
        "Heure début créneaux programmés",
        default=8,
        help_text="Heure (0–23) à partir de laquelle on propose des créneaux programmés.",
    )
    scheduled_window_end_hour = models.PositiveSmallIntegerField(
        "Heure fin créneaux programmés",
        default=20,
        help_text="Heure (0–23) à partir de laquelle on arrête les créneaux programmés.",
    )

    created_at = models.DateTimeField("Créée le", auto_now_add=True)
    updated_at = models.DateTimeField("Mise à jour le", auto_now=True)

    class Meta:
        verbose_name = "Configuration logistique"
        verbose_name_plural = "Configurations logistiques"

    def __str__(self):
        base = self.name or "Config logistique"
        if self.city:
            return f"{base} ({self.city})"
        return base

    @classmethod
    def current(cls):
        """
        Retourne la configuration active principale.
        Pour l’instant : la première config active, sinon la première tout court, sinon None.
        """
        config = cls.objects.filter(is_active=True).order_by("id").first()
        if config:
            return config
        return cls.objects.order_by("id").first()

    # ======================
    #  HELPERS PHASE 1/2
    # ======================
    def compute_express_extra(self, base_amount):
        """
        Calcule le supplément express à partir d'un montant de base
        (ss-total prestations + service FAGNI, par ex.).
        """
        if not self.express_enabled:
            return 0

        from decimal import Decimal

        if base_amount is None:
            base_amount = Decimal("0")
        if not isinstance(base_amount, Decimal):
            base_amount = Decimal(str(base_amount))

        extra = Decimal("0")

        # % du montant de base
        if self.express_extra_percent and self.express_extra_percent > 0:
            extra += (base_amount * (self.express_extra_percent / Decimal("100")))

        # Montant fixe
        if self.express_extra_flat and self.express_extra_flat > 0:
            extra += self.express_extra_flat

        # On peut arrondir à l'entier le plus proche (FCFA)
        return extra.quantize(Decimal("1"))

    def can_guarantee_express_for_pickup_hour(self, pickup_hour):
        """
        Indique si, pour une heure de collecte (0–23),
        l'express est théoriquement encore garanti (avant cutoff).
        """
        try:
            h = int(pickup_hour)
        except (TypeError, ValueError):
            return False

        return h <= self.pickup_cutoff_hour


# =====================
#  COMMANDE
# =====================
class Order(models.Model):

    # ======================
    #  FACTURATION (Lot 4.11.1)
    # ======================
    INVOICE_STATUS_CHOICES = [
        ("draft", "Brouillon"),
        ("issued", "Émise"),
        ("paid", "Payée"),
        ("canceled", "Annulée"),
    ]

    invoice_number = models.CharField(
        "Numéro de facture",
        max_length=30,
        unique=True,
        null=True,
        blank=True,
        help_text="Numéro unique de facture FAGNI (ex: FGN-2025-000123).",
    )

    invoice_date = models.DateTimeField(
        "Date de facture",
        null=True,
        blank=True,
        help_text="Date d’émission officielle de la facture.",
    )

    invoice_status = models.CharField(
        "Statut de la facture",
        max_length=20,
        choices=INVOICE_STATUS_CHOICES,
        default="draft",
    )

    # ======================
    #  FNE (Facture Normalisée) — LOT K
    # ======================
    FNE_STATUS_CHOICES = [
        ("disabled", "Désactivée"),
        ("pending", "À envoyer"),
        ("sent", "Envoyée"),
        ("accepted", "Acceptée"),
        ("rejected", "Rejetée"),
        ("error", "Erreur"),
    ]

    fne_status = models.CharField(
        "Statut FNE",
        max_length=20,
        choices=FNE_STATUS_CHOICES,
        default="disabled",
        help_text="Suivi interne de la synchro FNE (sans intégration API pour l’instant).",
    )

    fne_invoice_number = models.CharField(
        "N° FNE (numéro normalisé)",
        max_length=64,
        blank=True,
        null=True,
        help_text="Numéro renvoyé par la plateforme FNE après émission/validation.",
    )

    fne_uid = models.CharField(
        "UID FNE",
        max_length=128,
        blank=True,
        null=True,
        help_text="Identifiant unique retourné par la plateforme FNE (si applicable).",
    )

    fne_qr_data = models.TextField(
        "Données QR FNE",
        blank=True,
        null=True,
        help_text="Données/URL QR officiel FNE (si fourni).",
    )

    fne_sent_at = models.DateTimeField(
        "FNE envoyée le",
        blank=True,
        null=True,
    )

    fne_synced_at = models.DateTimeField(
        "FNE synchronisée le",
        blank=True,
        null=True,
        help_text="Date de retour/confirmation (acceptée/rejetée).",
    )

    fne_error = models.TextField(
        "Erreur FNE",
        blank=True,
        null=True,
        help_text="Message d’erreur si rejet/erreur technique.",
    )

    fne_payload = models.JSONField(
        "Payload FNE (audit)",
        blank=True,
        null=True,
        help_text="Copie du payload envoyé / réponse (audit interne).",
    )

    # ======================
    #  STATUT COMMANDE
    # ======================
    STATUS_CHOICES = [
        ("pending", "En attente"),
        ("in_progress", "En cours"),
        ("done", "Terminée"),
        ("canceled", "Annulée"),
    ]

    PAYMENT_STATUS_CHOICES = [
        ("unpaid", "Non payée"),
        ("paid", "Payée"),
        ("refunded", "Remboursée"),
        ("partially_refunded", "Partiellement remboursée"),
    ]

    # === MODES DE COLLECTE ===
    PICKUP_MODE_NOW = "now"
    PICKUP_MODE_LATER = "later"
    PICKUP_MODE_CHOICES = [
        (PICKUP_MODE_NOW, "Immédiate (dès que possible)"),
        (PICKUP_MODE_LATER, "Programmée"),
    ]

    # === MODES DE LIVRAISON ===
    DELIVERY_MODE_STANDARD = "standard"
    DELIVERY_MODE_EXPRESS = "express"
    DELIVERY_MODE_SCHEDULED = "scheduled"
    DELIVERY_MODE_CHOICES = [
        (DELIVERY_MODE_STANDARD, "Standard"),
        (DELIVERY_MODE_EXPRESS, "Express"),
        (DELIVERY_MODE_SCHEDULED, "Programmée"),
    ]

    # --------- Statut ---------
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        verbose_name="Statut",
    )

    # ----- Modes de collecte & livraison -----
    pickup_mode = models.CharField(
        "Mode de collecte",
        max_length=20,
        choices=PICKUP_MODE_CHOICES,
        default=PICKUP_MODE_NOW,
        help_text="Immédiate ou programmée.",
    )

    delivery_mode = models.CharField(
        "Mode de livraison",
        max_length=20,
        choices=DELIVERY_MODE_CHOICES,
        default=DELIVERY_MODE_STANDARD,
        help_text="Standard 48h, express 24h ou livraison programmée.",
    )

    scheduled_delivery_at = models.DateTimeField(
        "Date/heure de livraison programmée (legacy)",
        null=True,
        blank=True,
        help_text="Ancien champ – pourra être supprimé plus tard au profit des champs date/heure séparés.",
    )

    express_extra_fee = models.DecimalField(
        "Supplément express (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Part du total client liée au mode express (si applicable).",
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

    # --------- Paiement client ---------
    payment_status = models.CharField(
        "Statut de paiement",
        max_length=30,
        choices=PAYMENT_STATUS_CHOICES,
        default="unpaid",
    )

    amount_paid = models.DecimalField(
        "Montant effectivement payé (TTC, FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    payment_date = models.DateTimeField(
        "Date de paiement",
        null=True,
        blank=True,
    )

    payment_reference = models.CharField(
        "Référence paiement (PSP)",
        max_length=120,
        blank=True,
        null=True,
        help_text="Référence renvoyée par la passerelle de paiement.",
    )

    payment_method = models.CharField(
        "Moyen de paiement",
        max_length=50,
        blank=True,
        null=True,
        help_text="Ex : carte, mobile money, OM, Moov, etc.",
    )

    # ======================
    #  TVA & FACTURATION (Lot 4.11.2)
    # ======================

    vat_rate = models.DecimalField(
        "Taux TVA (%)",
        max_digits=5,
        decimal_places=2,
        default=18,
        help_text="Taux de TVA appliqué au revenu FAGNI.",
    )

    vat_base = models.DecimalField(
        "Base taxable TVA (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Montant HT soumis à TVA (revenu FAGNI uniquement).",
    )

    # --------- Montants de base ---------
    total = models.DecimalField(
        "Total prestations (base)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    service_fee = models.DecimalField(
        "Service FAGNI (HT, FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    # --- ADRESSES SPÉCIFIQUES (pickup / delivery) ---
    pickup_address = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Adresse de collecte (si différente de l'adresse client)."
    )
    pickup_lat = models.FloatField(blank=True, null=True)
    pickup_lng = models.FloatField(blank=True, null=True)

    delivery_address = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Adresse de livraison (si différente de l'adresse client)."
    )
    delivery_lat = models.FloatField(blank=True, null=True)
    delivery_lng = models.FloatField(blank=True, null=True)

    # --- DISTANCES DÉTAILLÉES ---
    distance_km_pickup = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Distance aller (client → blanchisserie) en km."
    )
    distance_km_delivery = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Distance retour (blanchisserie → client) en km."
    )
    distance_km_total = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Distance totale parcourue sur la course."
    )

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

    logistic_margin = models.IntegerField(
        "Marge logistique FAGNI",
        default=0,
    )

    # --------- Montants dérivés FAGNI ---------
    prestation_total = models.DecimalField(
        "Total prestations modèle FAGNI (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    fagni_revenue_ht = models.DecimalField(
        "Revenu FAGNI HT (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    fagni_revenue_ttc = models.DecimalField(
        "Revenu FAGNI TTC (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    commission_laundry_ht = models.DecimalField(
        "Commission FAGNI lavage HT (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    commission_delivery_ht = models.DecimalField(
        "Commission FAGNI livraison HT (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    vat_fagni = models.DecimalField(
        "TVA FAGNI (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    total_client_ttc = models.DecimalField(
        "Total client TTC (modèle FAGNI, FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    amount_laundry_partner = models.DecimalField(
        "Montant blanchisseur (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    amount_driver_partner = models.DecimalField(
        "Montant livreur (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    # ----- LOGISTIQUE : MODES & PROGRAMMATION -----
    pickup_scheduled_date = models.DateField(
        "Date collecte programmée",
        blank=True,
        null=True,
    )
    pickup_scheduled_time = models.TimeField(
        "Heure collecte programmée",
        blank=True,
        null=True,
    )

    delivery_scheduled_date = models.DateField(
        "Date livraison programmée",
        blank=True,
        null=True,
    )
    delivery_scheduled_time = models.TimeField(
        "Heure livraison programmée",
        blank=True,
        null=True,
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

    wallets_distributed = models.BooleanField(
        "Montants distribués dans les wallets",
        default=False,
    )

    driver_wallet_credited = models.BooleanField(
        "Wallet livreur déjà crédité",
        default=False,
    )


    def recompute_distances_from_positions(self):
        """
        Recalcule distance_km_pickup / distance_km_delivery / distance_km_total / distance_km
        en utilisant les coordonnées connues (client/pickup, livraison, blanchisserie).
        """
        origin_lat = None
        origin_lng = None
        if self.pickup_lat is not None and self.pickup_lng is not None:
            origin_lat = self.pickup_lat
            origin_lng = self.pickup_lng
        elif self.customer and self.customer.latitude is not None and self.customer.longitude is not None:
            origin_lat = self.customer.latitude
            origin_lng = self.customer.longitude

        laundry_lat = None
        laundry_lng = None
        if self.laundry_partner:
            if hasattr(self.laundry_partner, "latitude"):
                laundry_lat = self.laundry_partner.latitude
            if hasattr(self.laundry_partner, "longitude"):
                laundry_lng = self.laundry_partner.longitude

        if self.delivery_lat is not None and self.delivery_lng is not None:
            delivery_lat = self.delivery_lat
            delivery_lng = self.delivery_lng
        else:
            delivery_lat = origin_lat
            delivery_lng = origin_lng

        d_pickup = haversine_distance_km(origin_lat, origin_lng, laundry_lat, laundry_lng) if origin_lat is not None and laundry_lat is not None else None
        d_delivery = haversine_distance_km(laundry_lat, laundry_lng, delivery_lat, delivery_lng) if laundry_lat is not None and delivery_lat is not None else None

        if d_pickup is None:
            d_pickup = Decimal("0")
        if d_delivery is None:
            d_delivery = Decimal("0")

        self.distance_km_pickup = d_pickup
        self.distance_km_delivery = d_delivery
        self.distance_km_total = d_pickup + d_delivery
        self.distance_km = self.distance_km_total

    class Meta:
        verbose_name = "Commande"
        verbose_name_plural = "Commandes"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code or 'SANS-CODE'} - {self.customer}"

    # ========= FINANCE FAGNI =========
    def update_financials(self, save: bool = True):
        """
        Recalcule tous les montants financiers FAGNI pour cette commande
        à partir du moteur central compute_order_financials().
        """
        from .finance import compute_order_financials

        # 0) Recalcul distance depuis les DeliveryLeg si possible
        try:
            from .services import recompute_order_distance_from_legs
            recompute_order_distance_from_legs(self, save=True)
        except Exception:
            pass

        data = compute_order_financials(self)

        # 1) Prestations
        self.prestation_total = data.get("prestation_total", Decimal("0"))

        # 2) Livraison (transport facturé au client, hors express)
        self.delivery_fee = data.get("delivery_fee_client", Decimal("0"))
        self.driver_logistic_cost = data.get("delivery_cost_driver", Decimal("0"))

        # 3) Service FAGNI & express
        self.service_fee = data.get("service_fee_ht", Decimal("0"))

        # ✅ IMPORTANT : express_extra_fee = montant facturé au client pour l'option express
        if hasattr(self, "express_extra_fee"):
            self.express_extra_fee = data.get("express_for_client", data.get("express_extra_fee_client", Decimal("0")))

        # 4) Commissions partenaires
        self.commission_laundry_ht = data.get("commission_laundry_ht", Decimal("0"))
        self.commission_delivery_ht = data.get("commission_delivery_ht", Decimal("0"))

        # 4.b Montants à payer aux partenaires (SOURCE WALLETS)
        self.amount_laundry_partner = (
            data.get("commission_laundry_ht", Decimal("0"))
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        self.amount_driver_partner = (
            data.get("delivery_cost_driver", Decimal("0"))
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        # 5) Marge logistique FAGNI
        margin_delivery = data.get("margin_delivery", Decimal("0"))
        try:
            self.logistic_margin = int(margin_delivery)
        except (TypeError, ValueError):
            self.logistic_margin = 0

        # 6) Revenus FAGNI & TVA
        self.fagni_revenue_ht = data.get("fagni_revenue_ht", Decimal("0"))
        self.vat_fagni = data.get("vat_fagni", Decimal("0"))

        self.fagni_revenue_ttc = (
            self.fagni_revenue_ht + self.vat_fagni
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        # 7) Total TTC client (OFFICIEL)
        self.total_client_ttc = data.get("total_client_ttc", data.get("total_ttc_client", Decimal("0")))

        # ✅ Compat : ancien champ total = total client TTC
        # (sinon certaines pages peuvent afficher un total incomplet)
        self.total = self.total_client_ttc

        # 8) Facture (si payé)
        if self.payment_status == "paid":
            # 8.a Numéro de facture
            if not self.invoice_number:
                self.invoice_number = generate_invoice_number()

            # 8.b Date d'émission (première fois uniquement)
            if not getattr(self, "invoice_date", None):
                self.invoice_date = timezone.now()

            # 8.c Statut facture : payé => paid
            inv_st = getattr(self, "invoice_status", None)
            if inv_st in (None, "", "draft", "issued"):
                self.invoice_status = "paid"

        # ✅ LOT L : si payé => mettre FNE "pending" (sans casser si déjà traité)
        if self.payment_status == "paid":
            current = getattr(self, "fne_status", None) or "disabled"
            if current in ("disabled", "error", "rejected"):
                self.fne_status = "pending"

        if save:
            self.save()

        # ✅ Sync legs après calcul finance (delivery_fee / driver / margin)
        if save:
            try:
                sync_delivery_legs_for_order(self)
            except Exception:
                pass

        return data


    def compute_totals(self, save: bool = True):
        """
        Compatibilité : recalcule et met à jour les montants de la commande
        en utilisant le moteur de calcul FAGNI.
        """
        return self.update_financials(save=save)

    def mark_paid(
        self,
        method: str = "psp",
        reference: str | None = None,
        paid_at=None,
        amount=None,
        save: bool = True,
    ):
        """
        Porte d'entrée UNIQUE pour valider un paiement.
        - Remplit payment_status/payment_date/amount_paid
        - Remplit invoice_date/invoice_status
        - Recalcule les montants + génère invoice_number (via update_financials)
        - Déclenche la distribution via save() (payment_just_paid)
        Idempotent : si déjà payé, on complète juste les champs manquants.
        """
        from decimal import Decimal
        from django.utils import timezone

        # 1) Date de paiement
        if paid_at is None:
            paid_at = timezone.now()

        # 2) Assurer montants + invoice_number cohérents (sans save récursif)
        try:
            self.update_financials(save=False)
        except Exception:
            pass

        # 3) Statut payé
        self.payment_status = "paid"
        if hasattr(self, "payment_method"):
            self.payment_method = method or self.payment_method
        if hasattr(self, "payment_reference") and reference:
            self.payment_reference = reference

        # 4) payment_date (source de vérité)
        if getattr(self, "payment_date", None) is None:
            self.payment_date = paid_at

        # 5) amount_paid
        if amount is None:
            total_ttc = getattr(self, "total_client_ttc", None)
            total_ttc = Decimal(str(total_ttc)) if total_ttc not in (None, "", 0) else Decimal("0")
            amount = total_ttc
        try:
            self.amount_paid = Decimal(str(amount))
        except Exception:
            pass

        # 6) Facturation
        if getattr(self, "invoice_date", None) is None:
            self.invoice_date = self.payment_date or paid_at

        # On force un statut "paid" (tu as déjà tes choix: draft/issued/paid/canceled)
        if getattr(self, "invoice_status", None) in (None, "", "draft", "issued"):
            self.invoice_status = "paid"

        # 7) Persist + distribution (via save())
        if save:
            self.save()
        return self

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
        TVA sur les prestations seules (si besoin).
        Actuellement 18% de total_ht, indépendant de vat_fagni.
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
        Conservé pour compatibilité, mais le total officiel
        que le client paie est total_client_ttc.
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

        # 3) Prix client théorique
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

    def recompute_logistics_from_legs(self, save_legs: bool = True, save_order: bool = True):
        """
        Recalcule les montants logistiques (par jambe + agrégats Order)
        à partir des DeliveryLeg existantes.
        """
        legs = list(self.legs.all())
        if not legs:
            return

        delivery_fee = Decimal(self.delivery_fee or 0)
        driver_total = Decimal(self.amount_driver_partner or 0)
        margin_total = Decimal(self.logistic_margin or 0)

        if delivery_fee and not margin_total and driver_total:
            margin_total = delivery_fee - driver_total

        if delivery_fee and not driver_total and not margin_total:
            driver_total = delivery_fee

        if delivery_fee == 0 and driver_total == 0 and margin_total == 0:
            for leg in legs:
                leg.client_fee_share = Decimal("0")
                leg.driver_amount = Decimal("0")
                leg.fagni_margin = Decimal("0")
                if save_legs:
                    leg.save(update_fields=["client_fee_share", "driver_amount", "fagni_margin"])

            if save_order:
                self.amount_driver_partner = Decimal("0")
                self.logistic_margin = Decimal("0")
                total_dist = sum([leg.distance_km or 0 for leg in legs])
                self.distance_km = total_dist or None
                self.save(update_fields=["amount_driver_partner", "logistic_margin", "distance_km"])
            return

        distances = [Decimal(leg.distance_km or 0) for leg in legs]
        total_dist = sum(distances)

        weights = []
        if total_dist > 0:
            for d in distances:
                if d > 0:
                    weights.append(d / total_dist)
                else:
                    weights.append(Decimal("0"))
        else:
            n = len(legs)
            if n == 0:
                return
            equal_weight = Decimal("1") / Decimal(str(n))
            weights = [equal_weight] * n

        client_shares = []
        driver_shares = []
        margin_shares = []

        for w in weights:
            client_shares.append(
                (delivery_fee * w).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            driver_shares.append(
                (driver_total * w).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            margin_shares.append(
                (margin_total * w).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )

        def fix_rounding(shares, target_total):
            diff = target_total - sum(shares)
            if diff != 0 and shares:
                shares[0] = shares[0] + diff
            return shares

        client_shares = fix_rounding(client_shares, delivery_fee)
        driver_shares = fix_rounding(driver_shares, driver_total)
        margin_shares = fix_rounding(margin_shares, margin_total)

        for idx, leg in enumerate(legs):
            leg.client_fee_share = client_shares[idx] if delivery_fee else Decimal("0")
            leg.driver_amount = driver_shares[idx] if driver_total else Decimal("0")
            leg.fagni_margin = margin_shares[idx] if margin_total else Decimal("0")
            if save_legs:
                leg.save(update_fields=["client_fee_share", "driver_amount", "fagni_margin"])

        if save_order:
            total_driver = sum([leg.driver_amount for leg in legs])
            total_margin = sum([leg.fagni_margin for leg in legs])
            total_dist = sum([leg.distance_km or 0 for leg in legs])

            self.amount_driver_partner = total_driver
            self.logistic_margin = total_margin
            self.distance_km = total_dist or None
            self.save(update_fields=["amount_driver_partner", "logistic_margin", "distance_km"])

    # ---------- Photos ----------
    @property
    def total_photos(self):
        """
        Nombre total de photos sur toutes les lignes de la commande.
        """
        return sum((item.photos.count() for item in self.items.all()), 0)

    def get_all_photos(self):
        """
        Retourne une liste de TOUTES les photos rattachées aux lignes de cette commande.
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
    def amount_due(self):
        """
        Montant restant dû par le client.
        Utilise en priorité total_client_ttc (modèle FAGNI),
        sinon retombe sur total_ttc (prestations seules).
        """
        base = self.total_client_ttc or self.total_ttc
        return (base - (self.amount_paid or Decimal("0.00"))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    @property
    def amount_driver_partner_resolved(self):
        """
        Montant à afficher pour le revenu livreur (app livreur).

        Priorité :
        1) amount_driver_partner (calcul officiel FAGNI, si > 0)
        2) si des DeliveryLeg existent : somme des driver_amount
        3) sinon : fallback sur driver_logistic_cost
        """
        from decimal import Decimal, ROUND_HALF_UP

        # 1) Montant officiel sur la commande
        base = self.amount_driver_partner or Decimal("0")
        if base and base > 0:
            return base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 2) Sinon, on regarde les jambes de livraison (legs)
        try:
            legs_qs = self.legs.all()
        except Exception:
            legs_qs = None

        if legs_qs:
            total_legs = sum(
                (leg.driver_amount or Decimal("0"))
                for leg in legs_qs
            )
            if total_legs > 0:
                return total_legs.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 3) Fallback : coût logistique livreur
        cost = self.driver_logistic_cost or Decimal("0")
        return cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def distribute_mlm_commissions(self):
        """
        Distribue les commissions MLM sur 3 niveaux à partir du service_fee.
        """
        from mlm.models import ReferralLink, ReferralCommission, MLMSettings
        from wallets.models import Wallet, WalletTransaction

        if self.mlm_distributed:
            return

        if ReferralCommission.objects.filter(order=self).exists():
            if not self.mlm_distributed and self.pk:
                type(self).objects.filter(pk=self.pk).update(mlm_distributed=True)
                self.mlm_distributed = True
            return

        mlm_cfg = MLMSettings.get_active()
        if not mlm_cfg.enabled:
            return

        try:
            link = ReferralLink.objects.get(customer=self.customer)
        except ReferralLink.DoesNotExist:
            return

        upline = link.get_upline(levels=3)
        if not upline:
            return

        fee_base_decimal = self.service_fee or Decimal("0.00")
        if fee_base_decimal <= 0:
            return

        fee_base_decimal = fee_base_decimal.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        min_fee = Decimal(str(mlm_cfg.min_service_fee_for_commission or 0))
        if fee_base_decimal < min_fee:
            return

        fee_base_int = int(fee_base_decimal)

        percent_levels = [
            mlm_cfg.n1_percent or Decimal("0.00"),
            mlm_cfg.n2_percent or Decimal("0.00"),
            mlm_cfg.n3_percent or Decimal("0.00"),
        ]

        for idx, sponsor_link in enumerate(upline):
            if idx >= len(percent_levels):
                break

            sponsor_customer = getattr(sponsor_link, "customer", None)
            if sponsor_customer is None:
                continue

            percent = percent_levels[idx] or Decimal("0.00")
            if percent <= 0:
                continue

            rate = percent / Decimal("100")

            commission_decimal = (fee_base_decimal * rate).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            commission_int = int(commission_decimal)

            if commission_int <= 0:
                continue

            level = idx + 1

            ReferralCommission.objects.create(
                beneficiary_profile=sponsor_link,
                level=level,
                order=self,
                service_fee_base=fee_base_int,
                commission_percent=percent,
                commission_amount=commission_int,
            )

        from wallets.services import credit_wallet

        # (on garde ton wallet get_or_create)
        wallet, _ = Wallet.objects.get_or_create(
            owner_type="customer",
            customer=sponsor_customer,
            defaults={"currency": "XOF"},
        )

        commission_amount_decimal = Decimal(commission_int).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        credit_wallet(
            wallet,
            commission_amount_decimal,
            label=f"Commission niveau {level} pour commande {self.code}",
            order=self,
            tx_type="mlm_commission",
        )

        if self.pk:
            type(self).objects.filter(pk=self.pk).update(mlm_distributed=True)
        self.mlm_distributed = True

    def mark_as_paid_and_distribute(self):
        """
        À appeler quand la commande passe en statut payé.
        Distribue dans les wallets UNIQUEMENT si cela n'a jamais été fait.
        """
        from wallets.services import distribute_order_revenues

        if self.payment_status != "paid":
            return

        if getattr(self, "wallets_distributed", False):
            return

        distribute_order_revenues(self, recompute=True)

        # Après distribute_order_revenues(self, recompute=True)
        from orders.service_layer.payouts import trigger_driver_payout_for_leg

        legs_done = DeliveryLeg.objects.filter(order=self, status="done").select_related("driver")
        for leg in legs_done:
            trigger_driver_payout_for_leg(leg)

        self.wallets_distributed = True
        super(Order, self).save(update_fields=["wallets_distributed"])

    def pay_driver_if_needed(self):
        """
        Paie le livreur à la fin de course ("done"), seulement si la commande est payée.
        Anti-doublon basé sur WalletTransaction (source de vérité).
        """
        from decimal import Decimal
        from wallets.services import credit_wallet, get_or_create_wallet_for_delivery_partner
        from wallets.models import WalletTransaction

        # 1) La commande doit être payée
        if self.payment_status != "paid":
            return

        # 2) Il doit y avoir un livreur assigné
        if not self.delivery_partner:
            return

        wallet = get_or_create_wallet_for_delivery_partner(self.delivery_partner)

        # 3) 🔐 ANTI-DOUBLON DUR (SOURCE DE VÉRITÉ)
        if WalletTransaction.objects.filter(
            wallet=wallet,
            order=self,
            type="payout",
            direction="in",
        ).exists():
            # on synchronise le flag si besoin
            if not getattr(self, "driver_wallet_credited", False):
                self.driver_wallet_credited = True
                self.save(update_fields=["driver_wallet_credited"])
            return

        # 4) Montant à payer
        amount = self.amount_driver_partner_resolved or Decimal("0")
        if amount <= 0:
            return

        # 5) Paiement effectif
        credit_wallet(
            wallet,
            amount,
            label=f"Commande {self.code} – paiement livreur (fin de course)",
            order=self,
            tx_type="payout",
        )

        # 6) Flag cache
        self.driver_wallet_credited = True
        self.save(update_fields=["driver_wallet_credited"])

    def financial_timeline(self):
        """
        Timeline financière (lecture seule) pour affichage UI / admin.
        Cherche les événements clés via :
        - payment_status / amount_paid
        - WalletTransaction liées à la commande (laundry/internal/driver)
        """
        from wallets.models import WalletTransaction

        events = []

        def add_event(key, title, ok=False, details=None, at=None):
            events.append({
                "key": key,
                "title": title,
                "ok": bool(ok),
                "details": details or "",
                "at": at,
            })

        # 1) Paiement client
        paid = (getattr(self, "payment_status", "unpaid") == "paid")
        amount_paid = getattr(self, "amount_paid", None)
        total_ttc = getattr(self, "total_client_ttc", None)
        add_event(
            "payment",
            "Client payé",
            ok=paid,
            details=f"amount_paid={amount_paid} / total_client_ttc={total_ttc}",
            at=None,
        )

        # 2) Transactions wallets (si le modèle a created_at, on le remonte)
        txs = (
            WalletTransaction.objects
            .filter(order=self)
            .select_related("wallet")
            .order_by("id")
        )

        # Helpers pour trouver une tx spécifique
        def first_tx(owner_type=None, tx_type=None, direction=None):
            qs = txs
            if owner_type:
                qs = qs.filter(wallet__owner_type=owner_type)
            if tx_type:
                qs = qs.filter(type=tx_type)
            if direction:
                qs = qs.filter(direction=direction)
            return qs.first()

        # 2.a Blanchisserie (payout in)
        laundry_tx = first_tx(owner_type="laundry", tx_type="payout", direction="in")
        add_event(
            "laundry",
            "Blanchisserie créditée",
            ok=bool(laundry_tx),
            details=(laundry_tx.description if laundry_tx else ""),
            at=getattr(laundry_tx, "created_at", None) if laundry_tx else None,
        )

        # 2.b FAGNI interne (credit in)
        internal_tx = first_tx(owner_type="internal", tx_type="credit", direction="in")
        add_event(
            "internal",
            "FAGNI encaissé (wallet interne)",
            ok=bool(internal_tx),
            details=(internal_tx.description if internal_tx else ""),
            at=getattr(internal_tx, "created_at", None) if internal_tx else None,
        )

        # 2.c Livreur (payout in) — doit arriver fin de course
        driver_tx = first_tx(owner_type="driver", tx_type="payout", direction="in")
        driver_flag = bool(getattr(self, "driver_wallet_credited", False))
        add_event(
            "driver",
            "Livreur crédité",
            ok=bool(driver_tx) or driver_flag,
            details=(driver_tx.description if driver_tx else f"driver_wallet_credited={driver_flag}"),
            at=getattr(driver_tx, "created_at", None) if driver_tx else None,
        )

        return events

    def save(self, *args, **kwargs):
        from decimal import Decimal
        from django.utils import timezone

        payment_just_paid = False
        status_just_done = False

        old = None
        if self.pk:
            old = Order.objects.filter(pk=self.pk).first()
            if old:
                if old.payment_status != "paid" and self.payment_status == "paid":
                    payment_just_paid = True
                if old.status != "done" and self.status == "done":
                    status_just_done = True

        super().save(*args, **kwargs)

        # ============================================================
        # ✅ LOT 0.3 — COHÉRENCE PAIEMENT + FACTURE (anti trous)
        # ============================================================
        try:
            st = (getattr(self, "payment_status", "") or "").strip().lower()
            if st in ("paid", "completed", "succeeded"):
                updates = {}

                # 1) total TTC de référence
                total_ttc = getattr(self, "total_client_ttc", None)
                try:
                    total_ttc = Decimal(str(total_ttc)) if total_ttc not in (None, "", 0) else Decimal("0")
                except Exception:
                    total_ttc = Decimal("0")

                # 2) amount_paid cohérent
                amount_paid = getattr(self, "amount_paid", None)
                try:
                    amount_paid = Decimal(str(amount_paid)) if amount_paid not in (None, "", 0) else Decimal("0")
                except Exception:
                    amount_paid = Decimal("0")

                if total_ttc > 0 and amount_paid <= 0:
                    updates["amount_paid"] = total_ttc
                    self.amount_paid = total_ttc

                # 3) payment_date non null (source de vérité)
                if getattr(self, "payment_date", None) is None:
                    # priorité : old.payment_date si existait, sinon now()
                    pd = None
                    if old and getattr(old, "payment_date", None):
                        pd = old.payment_date
                    else:
                        pd = timezone.now()
                    updates["payment_date"] = pd
                    self.payment_date = pd

                # 4) invoice_date non null (facture alignée)
                if getattr(self, "invoice_date", None) is None:
                    invd = self.payment_date or getattr(self, "updated_at", None) or timezone.now()
                    updates["invoice_date"] = invd
                    self.invoice_date = invd

                # 5) invoice_status cohérent
                inv_st = getattr(self, "invoice_status", None)
                if inv_st in (None, "", "draft", "issued"):
                    updates["invoice_status"] = "paid"
                    self.invoice_status = "paid"

                # ⚠️ update DB sans recursion
                if updates:
                    Order.objects.filter(pk=self.pk).update(**updates)

        except Exception:
            pass

        # 1) Distribution générale (blanchisseur + interne) au paiement
        if payment_just_paid:
            self.mark_as_paid_and_distribute()

        # 2) Paiement livreur (DÉSACTIVÉ : payout driver géré par legs)
        # payout driver géré par legs (orders.views._trigger_driver_payout_for_leg)
        # if status_just_done or (payment_just_paid and self.status == "done"):
        #     self.pay_driver_if_needed()


def generate_invoice_number():
    """
    Génère un numéro de facture unique FAGNI.
    Format : FAGNI-YYYYMM-XXXX
    Exemple : FAGNI-202512-0007
    """
    today = timezone.now()
    prefix = f"FAGNI-{today.strftime('%Y%m')}"

    last = (
        Order.objects
        .filter(invoice_number__startswith=prefix)
        .order_by("-invoice_number")
        .first()
    )

    last_seq = 0
    if last and last.invoice_number:
        try:
            last_seq = int(last.invoice_number.split("-")[-1])
        except Exception:
            last_seq = 0

    return f"{prefix}-{(last_seq + 1):04d}"


# =====================
#  JAMBES DE LIVRAISON
# =====================
class DeliveryLeg(models.Model):
    """
    Une "jambe" de livraison pour une commande FAGNI.
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
        null=True,
        blank=True,
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

    distance_km = models.DecimalField(
        "Distance (km)",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )

    client_fee_share = models.DecimalField(
        "Part client (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    driver_amount = models.DecimalField(
        "Montant livreur (FCFA)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )

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


class Payment(models.Model):
    order = models.ForeignKey("Order", on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=0)
    channel = models.CharField(max_length=20)  # orange, mtn, wave
    reference = models.CharField(max_length=120, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_payments",
    )
    source = models.CharField(max_length=20, default="driver")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.order.code} - {self.amount} FCFA"
