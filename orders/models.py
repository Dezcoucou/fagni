from decimal import Decimal, ROUND_HALF_UP
import uuid
import math
from orders.utils.distances import haversine_distance_km
from django.db import models
from django.db import transaction
from django.db.models import Sum, F, DecimalField
from django.conf import settings
from django.utils import timezone
from partners.models import LaundryPartner, DeliveryPartner, RelayPointPartner
from .finance import compute_order_financials
from .services import recompute_order_distance_from_legs


def _round_fcfa(value):
    value = Decimal(str(value or 0))
    return value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

# ============================
#  SYNC DES JAMBES DE LIVRAISON
# ============================
def sync_delivery_legs_for_order(order):
    """
    Crée / synchronise les 2 jambes logistiques standard:
    - pickup
    - return

    Règles:
    - Split 50/50 (avec correction de rounding sur la 2e jambe)
    - Ne jamais rétrograder un leg déjà avancé (done/in_progress/canceled)
    - return ne doit pas être "assigned" tant que pickup n'est pas "done"
    - Si un payout tx existe déjà pour un leg, driver_amount est verrouillé sur tx.amount
    - En cas de doublons pickup/return, garder en priorité celui qui a un payout tx, sinon statut puis id
    """
    from decimal import Decimal, ROUND_HALF_UP
    from django.db.models import Sum
    from orders.models import DeliveryLeg  # safe local import

    # Legs existants
    legs_existing = list(
        DeliveryLeg.objects.filter(order=order).select_related("driver")
    )

    # Config de répartition
    delivery_fee = Decimal(str(getattr(order, "delivery_fee", 0) or 0))

    # ✅ source de vérité driver total: amount_driver_partner (fallback legacy driver_logistic_cost)
    driver_total = Decimal(str(getattr(order, "amount_driver_partner", None) or 0))
    if driver_total <= 0:
        driver_total = Decimal(str(getattr(order, "driver_logistic_cost", 0) or 0))

    margin_total = Decimal(str(getattr(order, "logistic_margin", 0) or 0))

    distance_total = Decimal(str(getattr(order, "distance_km", 0) or 0))
    distance_one_way = (distance_total / Decimal("2")).quantize(Decimal("0.01")) if distance_total else Decimal("0.00")

    client_share_1 = _round_fcfa(delivery_fee / 2)
    client_share_2 = _round_fcfa(delivery_fee - client_share_1)

    driver_share_1 = _round_fcfa(driver_total / 2)
    driver_share_2 = _round_fcfa(driver_total - driver_share_1)

    margin_share_1 = _round_fcfa(margin_total / 2)
    margin_share_2 = _round_fcfa(margin_total - margin_share_1)

    # Statut de base des jambes
    st = (getattr(order, "status", None) or "pending").lower()
    if st == "done":
        base_status = "done"
    elif st == "in_progress":
        base_status = "assigned"
    else:
        base_status = "pending"

    legs_data = [
        ("pickup", client_share_1, driver_share_1, margin_share_1),
        ("return", client_share_2, driver_share_2, margin_share_2),
    ]

    _status_rank = {"done": 5, "in_progress": 4, "assigned": 3, "pending": 2, "canceled": 1}

    # Helper payout
    def _paid_amount_for_leg(leg):
        try:
            from wallets.models import WalletTransaction
            tx = (
                WalletTransaction.objects.filter(
                    order=order,
                    leg=leg,
                    type="payout",
                    direction="in",
                )
                .order_by("id")
                .first()
            )
            return tx.amount if tx else None
        except Exception:
            return None

    def _has_payout_tx(leg):
        return _paid_amount_for_leg(leg) is not None

    # Map existants par type, en gardant le "meilleur"
    existing_by_type = {}
    for leg in legs_existing:
        lt = getattr(leg, "leg_type", None)
        if lt not in {"pickup", "return"}:
            continue

        cur = existing_by_type.get(lt)
        if cur is None:
            existing_by_type[lt] = leg
            continue

        leg_paid = _has_payout_tx(leg)
        cur_paid = _has_payout_tx(cur)

        # PRIORITÉ 1: garder celui qui a déjà un payout
        if leg_paid and not cur_paid:
            existing_by_type[lt] = leg
            continue
        if cur_paid and not leg_paid:
            continue

        # PRIORITÉ 2: statut puis id
        rank = _status_rank.get((getattr(leg, "status", None) or "").lower(), 0)
        cur_rank = _status_rank.get((getattr(cur, "status", None) or "").lower(), 0)
        if (rank > cur_rank) or (rank == cur_rank and (getattr(leg, "id", 0) or 0) > (getattr(cur, "id", 0) or 0)):
            existing_by_type[lt] = leg

    # Annule les doublons pickup/return qui ne sont pas retenus
    for leg in legs_existing:
        lt = getattr(leg, "leg_type", None)
        if lt in {"pickup", "return"}:
            kept = existing_by_type.get(lt)
            if kept and getattr(leg, "id", None) != getattr(kept, "id", None):
                # Ne pas annuler un leg déjà payé; on annule l'autre
                if _has_payout_tx(leg):
                    continue

                if (getattr(leg, "status", None) or "").lower() != "canceled":
                    leg.status = "canceled"
                    # ✅ neutraliser les montants (jambe annulée = hors calcul + jamais payée)
                    try:
                        from decimal import Decimal
                        leg.client_fee_share = Decimal("0")
                        leg.driver_amount = Decimal("0")
                        leg.fagni_margin = Decimal("0")
                    except Exception:
                        pass
                    leg.save(update_fields=["status", "client_fee_share", "driver_amount", "fagni_margin"])

    # CANCEL legs legacy/protégés (autres types)
    for leg in legs_existing:
        lt = getattr(leg, "leg_type", None)
        if lt not in {"pickup", "return"}:
            if (getattr(leg, "status", None) or "").lower() != "canceled":
                # si payé (rare) on évite de casser l'historique
                # Ne pas annuler un leg déjà payé; on annule l'autre
                if _has_payout_tx(leg):
                    continue

                if (getattr(leg, "status", None) or "").lower() != "canceled":
                    leg.status = "canceled"
                    # ✅ neutraliser les montants (jambe annulée = hors calcul + jamais payée)
                    try:
                        from decimal import Decimal
                        leg.client_fee_share = Decimal("0")
                        leg.driver_amount = Decimal("0")
                        leg.fagni_margin = Decimal("0")
                    except Exception:
                        pass
                    leg.save(update_fields=["status", "client_fee_share", "driver_amount", "fagni_margin"])

    # RÈGLE (STRICTE) :
    # - la jambe "return" reste PENDING tant que la collecte (pickup) n'est pas DONE
    # - dès que pickup est DONE, "return" peut passer ASSIGNED si la commande est en cours (in_progress)
    pickup_leg = existing_by_type.get("pickup")
    pickup_done = bool(pickup_leg and (getattr(pickup_leg, "status", None) or "").lower() == "done")
    base_status_return = "done" if st == "done" else ("assigned" if pickup_done else "pending")

    for leg_type, client_part, driver_part, margin_part in legs_data:
        leg = existing_by_type.get(leg_type)

        desired_status = base_status
        if leg_type == "return":
            desired_status = base_status_return

        if leg:
            # ✅ Ne jamais écraser un driver existant par None
            if getattr(order, "delivery_partner_id", None):
                leg.driver_id = order.delivery_partner_id

            cur = (getattr(leg, "status", None) or "").lower()

            # 0) Si payout existe, ne jamais toucher au statut (on protège l'historique)
            status_changed = False

            if _has_payout_tx(leg):

                # 🔒 Statut VERROUILLÉ: une jambe payée doit rester DONE

                # (sinon incohérence: payout existant + jambe repassée pending/assigned)

                if cur != "done":

                    leg.status = "done"

                    status_changed = True
            # 1) Si leg est finalisé/démarré: on ne change PAS le statut,
            #    mais on peut recalculer les montants tant qu'il n'y a pas de payout.
            elif cur in {"done", "in_progress"}:
                pass
            elif cur in {"canceled"}:
                # ✅ Si leg annulé mais NON payé, on peut le réactiver via SQL (sinon freeze DeliveryLeg.save)
                if not _has_payout_tx(leg):
                    try:
                        # On ne réactive que vers des statuts "soft"
                        if desired_status in {"pending", "assigned"}:
                            DeliveryLeg.objects.filter(pk=leg.pk, status="canceled").update(status=desired_status)
                            leg.status = desired_status
                            cur = desired_status
                            status_changed = True
                    except Exception:
                        pass

            # 2) Sync métier autorisé : seulement pending/assigned (jamais done)
            else:
                # règle return : pending tant que pickup pas done
                # => peut downgrade assigned->pending (autorisé métier)
                allowed = {"pending", "assigned"}
                if desired_status in allowed and cur in allowed:
                      # ✅ RÈGLE: ne jamais rétrograder un leg (ex: pickup assigned -> pending)
                      # Exception métier UNIQUE:
                      #   return peut repasser pending tant que pickup n'est pas done.
                      if (leg_type == "return") and (not pickup_done) and (cur == "assigned") and (desired_status == "pending"):
                          leg.status = "pending"
                          status_changed = True
                      else:
                          # Pas de downgrade: on applique seulement si upgrade
                          cur_rank = _status_rank.get(cur, 0)
                          des_rank = _status_rank.get(desired_status, 0)
                          if des_rank > cur_rank:
                              leg.status = desired_status
                              status_changed = True
            leg.distance_km = distance_one_way
            leg.client_fee_share = client_part
            leg.fagni_margin = margin_part

            paid_amount = _paid_amount_for_leg(leg)
            if paid_amount is not None:
                leg.driver_amount = paid_amount
            else:
                leg.driver_amount = driver_part

            update_fields = ["distance_km", "client_fee_share", "driver_amount", "fagni_margin"]

            if status_changed:
                update_fields.insert(0, "status")

            if getattr(order, "delivery_partner_id", None):
                update_fields.insert(0, "driver")

            leg.save(update_fields=update_fields)


        else:
            DeliveryLeg.objects.create(
                order=order,
                driver_id=getattr(order, "delivery_partner_id", None),
                leg_type=leg_type,
                status=desired_status,
                distance_km=distance_one_way,
                client_fee_share=client_part,
                driver_amount=driver_part,
                fagni_margin=margin_part,
            )

    # Après sync legs: aligner statut commande
    try:
        from orders.models import sync_order_status_from_legs
        sync_order_status_from_legs(order, save=True)
    except Exception:
        pass

    keepers = {existing_by_type.get("pickup"), existing_by_type.get("return")}
    keepers = {k for k in keepers if k is not None}

    for leg in legs_existing:
        if leg in keepers:
            continue
        if getattr(leg, "leg_type", None) not in {"pickup", "return"}:
            continue

        # si pas de payout + pas done => on neutralise
        if (not _has_payout_tx(leg)) and leg.status in {"pending", "assigned"}:
            leg.status = "canceled"
            leg.save(update_fields=["status"])


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
            # ❌ Ne jamais forcer done depuis Order.status
            # done doit venir de l'action livreur uniquement
            pass

    return changed


def sync_order_status_from_legs(order, save=False):
    """
    Synchronise Order.status à partir des DeliveryLeg.

    Règles (FAGNI) :
    - ✅ DONE uniquement si pickup DONE ET return DONE
    - Sinon :
      - si au moins un leg non-canceled est assigned/in_progress/done => in_progress
      - sinon => pending
    """

    # Ne jamais toucher une commande annulée
    if getattr(order, "status", None) == "canceled":
        return getattr(order, "status", None) or "canceled"

    legs_all = DeliveryLeg.objects.filter(order=order)

    # Condition stricte pour DONE : pickup + return doivent être DONE
    pickup_done = legs_all.filter(leg_type="pickup", status="done").exists()
    return_done = legs_all.filter(leg_type="return", status="done").exists()

    if pickup_done and return_done:
        new_status = "done"
    else:
        legs_active = legs_all.exclude(status="canceled")
        any_started = legs_active.filter(status__in=["assigned", "in_progress", "done"]).exists()
        new_status = "in_progress" if any_started else "pending"

    if getattr(order, "status", None) != new_status:
        order.status = new_status
        if save:
            order.save(update_fields=["status"])

    return new_status
# =====================
#  ABONNEMENTS (PILOTE COCODY) — ADDITIF (NE SUPPRIME RIEN)
# =====================
class Subscription(models.Model):
    """
    Abonnement hebdomadaire par sac (pilot Cocody).
    Objectif: générer des collectes/livraisons planifiées SANS toucher au modèle Order existant.
    """

    PACK_CHOICES = [
        ("essential", "Essentiel"),
        ("comfort", "Confort"),
    ]

    BAG_SIZE_CHOICES = [
        ("S", "Sac S"),
        ("M", "Sac M"),
    ]

    WEEKDAY_CHOICES = [
        (0, "Lundi"),
        (1, "Mardi"),
        (2, "Mercredi"),
        (3, "Jeudi"),
        (4, "Vendredi"),
        (5, "Samedi"),
        (6, "Dimanche"),
    ]

    customer = models.ForeignKey(
        "Customer",
        on_delete=models.PROTECT,
        related_name="subscriptions",
        verbose_name="Client",
    )

    pack = models.CharField("Pack", max_length=20, choices=PACK_CHOICES, default="essential")
    bag_size = models.CharField("Taille sac", max_length=2, choices=BAG_SIZE_CHOICES, default="M")

    pickup_weekday = models.PositiveSmallIntegerField(
        "Jour collecte",
        choices=WEEKDAY_CHOICES,
        default=0,
        help_text="Jour fixe de collecte hebdomadaire.",
    )
    delivery_weekday = models.PositiveSmallIntegerField(
        "Jour livraison",
        choices=WEEKDAY_CHOICES,
        default=3,
        help_text="Jour fixe de livraison hebdomadaire.",
    )

    start_date = models.DateField("Date de début", null=True, blank=True)
    is_active = models.BooleanField("Actif", default=True)
    notes = models.CharField("Notes (optionnel)", max_length=255, blank=True, default="")

    created_at = models.DateTimeField("Créé le", auto_now_add=True)
    updated_at = models.DateTimeField("Mis à jour le", auto_now=True)

    class Meta:
        verbose_name = "Abonnement"
        verbose_name_plural = "Abonnements"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Abonnement {self.customer} ({self.get_pack_display()} / Sac {self.bag_size})"

    def _week_start(self, d):
        # Lundi comme début de semaine
        from datetime import timedelta
        return d - timedelta(days=d.weekday())

    def _next_date_for_weekday(self, week_start, weekday):
        from datetime import timedelta
        return week_start + timedelta(days=int(weekday))

    def generate_cycles(self, weeks_ahead: int = 4, save: bool = True) -> int:
        """
        Génère à l'avance des cycles (collecte+livraison) pour les prochaines semaines.
        Idempotent: ne recrée pas si déjà existant.
        """
        from datetime import timedelta
        from django.utils import timezone

        if not self.is_active:
            return 0

        today = timezone.localdate()
        base = self.start_date or today
        # on part de la semaine du "base"
        wk0 = self._week_start(base)

        created = 0
        for w in range(int(weeks_ahead)):
            ws = wk0 + timedelta(days=7*w)
            pickup_date = self._next_date_for_weekday(ws, self.pickup_weekday)
            delivery_date = self._next_date_for_weekday(ws, self.delivery_weekday)

            # garde-fou : livraison doit être >= collecte (sinon semaine suivante)
            if delivery_date < pickup_date:
                delivery_date = delivery_date + timedelta(days=7)

            obj, was_created = SubscriptionCycle.objects.get_or_create(
                subscription=self,
                week_start=ws,
                defaults={
                    "customer": self.customer,
                    "pickup_date": pickup_date,
                    "delivery_date": delivery_date,
                    "bag_size": self.bag_size,
                    "status": "COLLECTE",
                },
            )
            if was_created:
                created += 1

        return created


class SubscriptionCycle(models.Model):
    """
    Un cycle opérationnel (collecte/livraison) généré depuis un abonnement.
    On le traite comme "vérité terrain" pour le planning.
    """

    STATUS_CHOICES = [
        ("COLLECTE", "À collecter"),
        ("EN_TRAITEMENT", "En traitement"),
        ("EN_LIVRAISON", "En livraison"),
        ("LIVRE", "Livré"),
        ("ANNULE", "Annulé"),
    ]

    subscription = models.ForeignKey(
        "Subscription",
        on_delete=models.CASCADE,
        related_name="cycles",
        verbose_name="Abonnement",
    )

    # dénormalisation utile pour filtres/exports
    customer = models.ForeignKey(
        "Customer",
        on_delete=models.PROTECT,
        related_name="subscription_cycles",
        verbose_name="Client",
    )

    week_start = models.DateField("Semaine (lundi)", db_index=True)
    pickup_date = models.DateField("Date collecte", db_index=True)
    delivery_date = models.DateField("Date livraison", db_index=True)

    bag_size = models.CharField("Taille sac", max_length=2, default="M")
    status = models.CharField("Statut", max_length=20, choices=STATUS_CHOICES, default="COLLECTE")

    # Brouillon (Wizard client) : n’apparaît pas dans OPS tant que non validé
    is_draft = models.BooleanField(
        "Brouillon",
        default=False,
        db_index=True,
        help_text="Commande en cours de création côté client (wizard).",
    )

    # lien optionnel vers une commande existante si un jour tu veux "matérialiser" en Order
    related_order = models.ForeignKey(
        "Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscription_cycles",
        verbose_name="Commande liée (optionnel)",
    )

    created_at = models.DateTimeField("Créé le", auto_now_add=True)
    updated_at = models.DateTimeField("Mis à jour le", auto_now=True)

    class Meta:
        verbose_name = "Cycle abonnement"
        verbose_name_plural = "Cycles abonnement"
        ordering = ["-pickup_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "week_start"],
                name="uniq_subscription_week_start",
            ),
        ]

    def __str__(self):
        return f"{self.customer} — collecte {self.pickup_date} / livraison {self.delivery_date} ({self.status})"

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
        ("partial", "Partiellement payée"),
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
            recompute_order_distance_from_legs(self, save=False)
        except Exception:
            pass

        data = compute_order_financials(self)

        # 1) Prestations
        self.prestation_total = data.get("prestation_total", Decimal("0"))

        # 2) Livraison (transport facturé au client, hors express)
        # ------------------------------------------------------------
        # ✅ LOCK POOL (Option A)
        # Si delivery_fee == amount_driver_partner + logistic_margin et amount_driver_partner>0,
        # alors amount_driver_partner/logistic_margin/driver_logistic_cost deviennent source de vérité
        # et update_financials ne les écrase plus.
        # ------------------------------------------------------------
        try:
            _df_cur = Decimal(str(getattr(self, 'delivery_fee', 0) or 0))
            _dt_cur = Decimal(str(getattr(self, 'amount_driver_partner', 0) or 0))
            _mt_cur = Decimal(str(getattr(self, 'logistic_margin', 0) or 0))
            lock_pool = bool(_dt_cur > 0 and _df_cur > 0 and (_dt_cur + _mt_cur) == _df_cur)
        except Exception:
            lock_pool = False

        if lock_pool:
            # Pool verrouillé: on garde driver/margin et on recolle delivery_fee
            try:
                self.delivery_fee = Decimal(str(getattr(self, 'amount_driver_partner', 0) or 0)) + Decimal(str(getattr(self, 'logistic_margin', 0) or 0))
                # mirror legacy (utile si du code lit encore driver_logistic_cost)
                self.driver_logistic_cost = Decimal(str(getattr(self, 'amount_driver_partner', 0) or 0))
            except Exception:
                pass
        else:
            # Calcul normal (auto)
            self.delivery_fee = data.get('delivery_fee_client', Decimal('0'))
            self.driver_logistic_cost = data.get('delivery_cost_driver', Decimal('0'))
            # conserve la logique existante si data fournit amount_driver_partner
            try:
                if data.get('amount_driver_partner', None) is not None:
                    self.amount_driver_partner = data.get('amount_driver_partner')
                else:
                    self.amount_driver_partner = data.get('delivery_cost_driver', Decimal('0'))
            except Exception:
                self.amount_driver_partner = data.get('delivery_cost_driver', Decimal('0'))
        
            try:
                margin_delivery = data.get('margin_delivery', 0)
                self.logistic_margin = int(margin_delivery)
            except Exception:
                self.logistic_margin = 0
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
        Porte d'entrée UNIQUE pour enregistrer un paiement.
        - Enregistre un paiement (audit) via add_payment() si disponible
        - Recalcule amount_paid + payment_status à partir des paiements (source de vérité)
        - Si devient 'paid' => prépare invoice_date/invoice_status + update_financials()
        Idempotent / safe (ne doit pas casser même si modules manquent).
        """
        from decimal import Decimal
        from django.utils import timezone

        if paid_at is None:
            paid_at = timezone.now()

        # 0) Recalcule financiers (sans save récursif) pour avoir total_client_ttc correct
        try:
            self.update_financials(save=False)
        except Exception:
            pass

        total_ttc = Decimal(str(getattr(self, "total_client_ttc", 0) or 0))

        # 🔒 Total invalide => pas de "paid"
        if total_ttc <= 0:
            try:
                self.amount_paid = Decimal("0.00")
                self.payment_status = "unpaid"
                if save:
                    self.save(update_fields=["amount_paid", "payment_status"])
            except Exception:
                pass
            return self

        # 1) Montant payé
        try:
            amt = Decimal(str(amount)) if amount is not None else total_ttc
        except Exception:
            amt = Decimal("0")

        if amt < 0:
            amt = Decimal("0")

        # 2) Remplir métadonnées paiement (sans forcer 'paid' ici)
        try:
            if hasattr(self, "payment_method") and method:
                self.payment_method = method
            if hasattr(self, "payment_reference") and reference:
                self.payment_reference = reference
            if getattr(self, "payment_date", None) is None:
                self.payment_date = paid_at
        except Exception:
            pass

        # 3) Audit : add_payment si possible (FCFA entier)
        pay_amount = amt
        if pay_amount > total_ttc:
            pay_amount = total_ttc
        try:
            pay_amount_int = int(pay_amount)
        except Exception:
            pay_amount_int = 0

        if pay_amount_int > 0 and hasattr(self, "add_payment"):
            try:
                self.add_payment(
                    amount=pay_amount_int,
                    channel=(method or "psp"),
                    reference=(reference or ""),
                    source="system",
                    save=True,
                )
            except Exception:
                # Ne jamais casser mark_paid si l'audit échoue
                pass

        # 4) Sync canonique depuis Payments (source de vérité)
        just_became_paid = False
        try:
            just_became_paid = bool(self.sync_payment_status_from_payments(save=False))
        except Exception:
            # Fallback: si on a payé >= total => paid sinon partial/unpaid
            if pay_amount_int >= int(total_ttc):
                self.payment_status = "paid"
                self.amount_paid = total_ttc
                just_became_paid = True
            elif pay_amount_int > 0:
                self.payment_status = "partial"
                self.amount_paid = Decimal(str(pay_amount_int))
            else:
                self.payment_status = "unpaid"
                self.amount_paid = Decimal("0.00")

        # 5) Si on devient paid => préparer facture + recalcul finance (génère invoice_number etc.)
        if getattr(self, "payment_status", None) == "paid":
            try:
                if getattr(self, "invoice_date", None) is None:
                    self.invoice_date = getattr(self, "payment_date", None) or paid_at
                if getattr(self, "invoice_status", None) in (None, "", "draft", "issued"):
                    self.invoice_status = "paid"
            except Exception:
                pass

            try:
                # update_financials gère invoice_number + fne_status, + sync legs (si save=True)
                self.update_financials(save=False)
            except Exception:
                pass

        if save:
            try:
                fields = []
                for f in (
                    "payment_status",
                    "amount_paid",
                    "payment_date",
                    "payment_method",
                    "payment_reference",
                    "invoice_number",
                    "invoice_date",
                    "invoice_status",
                    "fne_status",
                ):
                    if hasattr(self, f):
                        fields.append(f)
                if fields:
                    self.save(update_fields=list(dict.fromkeys(fields)))
                else:
                    self.save()
            except Exception:
                pass

            # Sync legs après save si ton moteur en dépend (sécurisé)
            try:
                sync_delivery_legs_for_order(self)
            except Exception:
                pass

        return self


    def total_paid_from_payments(self):
        """
        Source de vérité : somme des Payment.amount (entiers FCFA).
        Retourne un Decimal.
        """
        from decimal import Decimal
        from django.db.models import Sum
        try:
            from .models import Payment
        except Exception:
            return Decimal("0.00")

        agg = Payment.objects.filter(order=self).aggregate(s=Sum("amount"))
        s = agg.get("s") or 0
        try:
            return Decimal(str(s))
        except Exception:
            return Decimal("0.00")


    def sync_payment_status_from_payments(self, save: bool = True):
        """
        Met à jour amount_paid + payment_status à partir des paiements (Payments).
        Règles:
        - total_client_ttc <= 0 => unpaid
        - amount_paid = min(sum(payments), total_client_ttc)
        - status: unpaid / partial / paid
        Retourne True si la commande vient juste de passer à 'paid'.
        """
        from decimal import Decimal
        from django.utils import timezone

        total_ttc = Decimal(str(getattr(self, "total_client_ttc", 0) or 0))
        old_status = (getattr(self, "payment_status", None) or "unpaid")

        if total_ttc <= 0:
            self.amount_paid = Decimal("0.00")
            self.payment_status = "unpaid"
            if save:
                self.save(update_fields=["amount_paid", "payment_status"])
            return False

        paid_sum = self.total_paid_from_payments()
        if paid_sum < 0:
            paid_sum = Decimal("0.00")

        effective_paid = paid_sum if paid_sum <= total_ttc else total_ttc
        if paid_sum <= 0:
            new_status = "unpaid"
        elif paid_sum < total_ttc:
            new_status = "partial"
        else:
            new_status = "paid"

        just_became_paid = (old_status != "paid" and new_status == "paid")

        self.amount_paid = effective_paid
        self.payment_status = new_status

        if just_became_paid:
            if getattr(self, "payment_date", None) is None:
                self.payment_date = timezone.now()
            if getattr(self, "invoice_date", None) is None:
                self.invoice_date = self.payment_date
            if getattr(self, "invoice_status", None) in (None, "", "draft", "issued"):
                self.invoice_status = "paid"

        if save:
            fields = ["amount_paid", "payment_status"]
            if just_became_paid:
                for f in ("payment_date", "invoice_date", "invoice_status"):
                    if hasattr(self, f):
                        fields.append(f)
            self.save(update_fields=list(dict.fromkeys(fields)))

        return just_became_paid
    @property
    def amount_remaining(self):
        try:
            total = Decimal(str(getattr(self, "total_client_ttc", 0) or 0))
        except Exception:
            total = Decimal("0.00")

        try:
            paid = Decimal(str(getattr(self, "amount_paid", 0) or 0))
        except Exception:
            paid = Decimal("0.00")

        if total <= 0:
            return Decimal("0.00")

        rem = total - paid
        if rem < 0:
            rem = Decimal("0.00")

        return rem.quantize(Decimal("0.01"))


    def add_payment(self, amount, channel="psp", reference="", source="driver", confirmed_by=None, save=True):
        """
        Enregistre un paiement (partiel ou total) avec garde-fous:
        - total_client_ttc == 0 => ValidationError
        - commande déjà soldée => None (ne crée rien)
        - surpaiement => cappé au reste dû
        - idempotent si reference fournie (order+reference unique soft via get_or_create)
        """
        from decimal import Decimal
        from django.core.exceptions import ValidationError
        from django.db.models import Sum
        from .models import Payment

        # normalisation amount -> int FCFA
        try:
            amt = int(Decimal(str(amount or 0)))
        except Exception:
            amt = 0
        if amt <= 0:
            return None

        channel = (channel or "psp")[:20]
        reference = (reference or "").strip()

        # refresh minimal (évite états stale)
        try:
            self.refresh_from_db(fields=["total_client_ttc", "payment_status", "amount_paid"])
        except Exception:
            pass

        total_ttc = Decimal(str(getattr(self, "total_client_ttc", 0) or 0))
        if total_ttc <= 0:
            raise ValidationError("Impossible d'enregistrer un paiement : total_client_ttc est à 0.")

        # source de vérité: somme des paiements existants
        paid_sum = Payment.objects.filter(order=self).aggregate(s=Sum("amount")).get("s") or 0
        paid_sum = Decimal(str(paid_sum))
        if paid_sum < 0:
            paid_sum = Decimal("0")

        remaining = total_ttc - paid_sum
        if remaining <= 0:
            # commande déjà soldée
            return None

        # cap au reste dû
        capped = Decimal(str(min(Decimal(str(amt)), remaining)))
        amt = int(capped)
        if amt <= 0:
            return None

        # idempotence: si reference existe déjà, on retourne l'existant (sans créer un nouveau)
        if reference:
            p, created = Payment.objects.get_or_create(
                order=self,
                reference=reference,
                defaults={"amount": amt, "channel": channel, "source": source, "confirmed_by": confirmed_by},
            )
            # si on vient de le créer mais qu'entre-temps remaining a changé, le Payment.save guard gérera.
            # normaliser champs (soft)
            upd = {}
            if p.amount != amt:
                upd["amount"] = amt
            if p.channel != channel:
                upd["channel"] = channel
            if p.source != source:
                upd["source"] = source
            if confirmed_by and p.confirmed_by_id != getattr(confirmed_by, "id", None):
                upd["confirmed_by"] = confirmed_by
            if upd:
                Payment.objects.filter(pk=p.pk).update(**upd)
        else:
            p = Payment.objects.create(
                order=self,
                amount=amt,
                channel=channel,
                reference="",
                source=source,
                confirmed_by=confirmed_by,
            )

        if save:
            self.sync_payment_status_from_payments(save=True)
        return p

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
            logi = getattr(settings, "FAGNI_LOGISTICS", {})
            driver_fixed_per_leg = Decimal(str(logi.get("driver_fixed_per_leg", 300)))

            # Minimum driver pour 2 jambes
            driver_min = (Decimal("2.0") * driver_fixed_per_leg).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # Le client paye au moins min
            self.distance_km = None
            self.driver_logistic_cost = driver_min

            # marge = min - driver_min (bornée >=0)
            margin = (client_min_fee - driver_min)
            if margin < 0:
                margin = Decimal("0.00")
            self.logistic_margin = int(margin)

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
        Recalcule les montants logistiques à partir des DeliveryLeg existantes.

        Garde-fous:
        - Exclut les legs canceled des calculs ET remet leurs montants à 0.
        - Répartit delivery_fee / driver_total / margin_total sur les legs actifs.
        - Si une tx payout existe pour une jambe:
            - driver_amount est VERROUILLÉ sur tx.amount
            - client_fee_share et fagni_margin sont FIGÉS (valeurs DB)
          (on répartit le reste sur les autres jambes non verrouillées)
        - Ne change pas les statuts et ne crée aucune transaction.
        """
        from decimal import Decimal, ROUND_HALF_UP

        # 0) Canceled -> montants à 0 (hors calcul)
        canceled = list(self.legs.filter(status="canceled"))
        if canceled:
            for leg in canceled:
                if (leg.client_fee_share or 0) != 0 or (leg.driver_amount or 0) != 0 or (leg.fagni_margin or 0) != 0:
                    leg.client_fee_share = Decimal("0")
                    leg.driver_amount = Decimal("0")
                    leg.fagni_margin = Decimal("0")
                    if save_legs:
                        leg.save(update_fields=["client_fee_share", "driver_amount", "fagni_margin"])

        legs = list(self.legs.exclude(status__in=["canceled"]).order_by("id"))
        if not legs:
            return

        def _round_fcfa(v: Decimal) -> Decimal:
            v = Decimal(str(v or 0))
            return v.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        def fix_rounding(shares, target_total: Decimal, allowed_idx=None):
            """
            Corrige le rounding pour que sum(shares)==target_total (en FCFA),
            en ajustant uniquement les indices allowed_idx (si fournis).
            """
            target_total = _round_fcfa(target_total)
            shares = [ _round_fcfa(s) for s in shares ]
            idxs = list(allowed_idx) if allowed_idx else list(range(len(shares)))
            if not idxs:
                return shares
            cur = sum([shares[i] for i in range(len(shares))], Decimal("0"))
            diff = target_total - cur
            if diff == 0:
                return shares
            # ajuster le dernier index autorisé
            last = idxs[-1]
            shares[last] = _round_fcfa(Decimal(str(shares[last])) + diff)
            return shares

        # 1) Totaux de référence (commande)
        delivery_fee = Decimal(str(getattr(self, "delivery_fee", 0) or 0))
        driver_total = Decimal(str(getattr(self, "amount_driver_partner", 0) or 0))
        margin_total = Decimal(str(getattr(self, "logistic_margin", 0) or 0))

        # ✅ Pool cohérent = source de vérité (ne pas écraser ces montants)
        lock_pool = False
        try:
            _df = Decimal(str(delivery_fee or 0))
            _dt = Decimal(str(self.amount_driver_partner or 0))
            _mt = Decimal(str(self.logistic_margin or 0))
            if _df > 0 and _round_fcfa(_dt + _mt) == _round_fcfa(_df):
                driver_total = _dt
                margin_total = _mt
                lock_pool = True
        except Exception:
            lock_pool = False

        driver_cost_field = Decimal(str(getattr(self, "driver_logistic_cost", 0) or 0))

        # Driver total: priorité amount_driver_partner, sinon driver_logistic_cost
        if (not lock_pool) and delivery_fee and (not driver_total) and driver_cost_field:
            driver_total = driver_cost_field

        # Inférences cohérentes si un des 3 manque
        if (not lock_pool) and delivery_fee and margin_total and (not driver_total):
            driver_total = delivery_fee - margin_total
        if (not lock_pool) and delivery_fee and driver_total and (not margin_total):
            margin_total = delivery_fee - driver_total

        if driver_total < 0:
            driver_total = Decimal("0")
        if margin_total < 0:
            margin_total = Decimal("0")

        # ✅ Invariant pool logistique (marge négative interdite)
        if (not lock_pool) and delivery_fee and driver_total > delivery_fee:
            driver_total = delivery_fee
            margin_total = Decimal("0")

        # Si juste delivery_fee (et rien d’autre) -> tout au driver
        if (not lock_pool) and delivery_fee and (not driver_total) and (not margin_total):
            driver_total = delivery_fee

        # Cas tout à 0 -> reset legs actifs
        if delivery_fee == 0 and driver_total == 0 and margin_total == 0:
            for leg in legs:
                leg.client_fee_share = Decimal("0")
                leg.driver_amount = Decimal("0")
                leg.fagni_margin = Decimal("0")
                if save_legs:
                    leg.save(update_fields=["client_fee_share", "driver_amount", "fagni_margin"])
            if save_order:
                total_dist = sum([leg.distance_km or 0 for leg in legs])
                self.amount_driver_partner = Decimal("0")
                self.logistic_margin = Decimal("0")
                self.distance_km = total_dist or None
                self.driver_logistic_cost = Decimal("0")
                self.save(update_fields=["amount_driver_partner", "logistic_margin", "distance_km", "driver_logistic_cost"])
            return

        # 2) Poids (distance sinon égal)
        distances = [Decimal(str(leg.distance_km or 0)) for leg in legs]
        total_dist = sum(distances)
        if total_dist > 0:
            weights = [(d / total_dist) if d > 0 else Decimal("0") for d in distances]
        else:
            n = len(legs)
            weights = [Decimal("1") / Decimal(str(n)) for _ in legs]

        # ✅ Re-verrouillage du pool (évite dérives)
        if lock_pool:
            try:
                driver_total = Decimal(str(self.amount_driver_partner or 0))
                margin_total = Decimal(str(self.logistic_margin or 0))
                delivery_fee = Decimal(str(self.delivery_fee or 0))
            except Exception:
                pass

        # 3) Verrouillage payout par jambe
        locked_driver_amount = {}
        locked_client_share = {}
        locked_margin = {}

        try:
            from wallets.models import WalletTransaction
            for leg in legs:
                tx = (
                    WalletTransaction.objects.filter(
                        order_id=self.id,
                        leg=leg,
                        type="payout",
                        direction="in",
                    )
                    .order_by("-id")
                    .first()
                )
                if tx:
                    locked_driver_amount[leg.id] = Decimal(str(tx.amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    # FIGER client/marge = valeurs DB actuelles
                    locked_client_share[leg.id] = Decimal(str(getattr(leg, "client_fee_share", 0) or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    locked_margin[leg.id] = Decimal(str(getattr(leg, "fagni_margin", 0) or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except Exception:
            locked_driver_amount = {}
            locked_client_share = {}
            locked_margin = {}

        def allocate_total(total: Decimal, locked: dict):
            """
            Alloue total sur legs avec:
            - locked: dict leg_id -> montant figé
            - reste distribué sur non-lock selon weights
            Retourne (shares_list, adjusted_total)
            """
            total = Decimal(str(total or 0))
            locked_sum = sum([Decimal(str(v or 0)) for v in locked.values()], Decimal("0")) if locked else Decimal("0")
            if locked_sum > total:
                total = locked_sum
            remaining = total - locked_sum
            n = len(legs)
            shares = [Decimal("0") for _ in range(n)]
            unlocked_idx = [i for i, lg in enumerate(legs) if lg.id not in locked]
            if unlocked_idx:
                if len(unlocked_idx) == 1:
                    shares[unlocked_idx[0]] = _round_fcfa(remaining)
                else:
                    wsum = sum([weights[i] for i in unlocked_idx], Decimal("0"))
                    for i in unlocked_idx:
                        w = weights[i]
                        if wsum > 0:
                            w = w / wsum
                        shares[i] = _round_fcfa(remaining * w)
                shares = fix_rounding(shares, remaining, allowed_idx=unlocked_idx)

            # inject locked
            for i, lg in enumerate(legs):
                if lg.id in locked:
                    shares[i] = _round_fcfa(locked[lg.id])

            # final correction to match total exactly (allow unlocked if possible else locked)
            allowed = unlocked_idx if unlocked_idx else [i for i, lg in enumerate(legs) if lg.id in locked]
            shares = fix_rounding(shares, total, allowed_idx=allowed)
            return shares, total

        # 🔒 Réalité > théorie : si payouts existent, on ne doit jamais réduire les totaux sous le lock
        # driver_total
        driver_shares, driver_total_adj = allocate_total(driver_total, locked_driver_amount)
        driver_total = driver_total_adj

        # delivery_fee (client)
        client_shares, delivery_fee_adj = allocate_total(delivery_fee, locked_client_share)
        delivery_fee = delivery_fee_adj

        # margin_total
        margin_shares, margin_total_adj = allocate_total(margin_total, locked_margin)
        margin_total = margin_total_adj

        # 4) Appliquer sur les legs
        for idx, leg in enumerate(legs):
            leg.client_fee_share = Decimal(str(client_shares[idx] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            # driver: payout lock > calcul (déjà injecté)
            leg.driver_amount = Decimal(str(driver_shares[idx] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            leg.fagni_margin = Decimal(str(margin_shares[idx] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            if save_legs:
                leg.save(update_fields=["client_fee_share", "driver_amount", "fagni_margin"])

        # 5) Mettre à jour la commande (sans créer de tx)
        if save_order:
            try:
                self.amount_driver_partner = Decimal(str(driver_total or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            except Exception:
                self.amount_driver_partner = Decimal("0")

            try:
                self.logistic_margin = int(_round_fcfa(margin_total))
            except Exception:
                self.logistic_margin = 0

            try:
                self.delivery_fee = Decimal(str(delivery_fee or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            except Exception:
                pass

            # mirror legacy
            try:
                self.driver_logistic_cost = Decimal(str(self.amount_driver_partner or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            except Exception:
                pass

            self.distance_km = total_dist or None
            self.save(update_fields=["amount_driver_partner", "logistic_margin", "delivery_fee", "driver_logistic_cost", "distance_km"])
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
        Compat legacy (NE PAS utiliser comme déclencheur principal).
        On délègue au moteur canonique mlm.services.generate_mlm_commissions_for_order().
        """
        try:
            from mlm.services import generate_mlm_commissions_for_order
            return generate_mlm_commissions_for_order(self)
        except Exception:
            return None

    def _has_partner_distribution(self):
        """
        True si au moins la distribution vers la blanchisserie est TRAÇABLE en WalletTransaction.

        On accepte credit OU payout (selon l'implémentation wallets.services).
        """
        from decimal import Decimal
        try:
            from wallets.models import WalletTransaction
        except Exception:
            return False

        laundry_amount = Decimal(str(getattr(self, "amount_laundry_partner", 0) or 0))
        if laundry_amount <= 0:
            return False

        return WalletTransaction.objects.filter(
            order=self,
            type__in=("credit", "payout"),
            direction="in",
            wallet__owner_type="laundry",
            amount=laundry_amount,
        ).exists()

    def _ensure_laundry_distribution_trace(self) -> bool:
        """
        Fallback de sécurité:
        - Si la tx blanchisserie (credit/payout in) n'existe pas, on la crée (idempotent),
          en créditant le wallet laundry + traçage WalletTransaction liée à la commande.
        """
        from decimal import Decimal, ROUND_HALF_UP
        try:
            from wallets.models import Wallet, WalletTransaction
        except Exception:
            return False

        laundry_partner_id = getattr(self, "laundry_partner_id", None)
        if not laundry_partner_id:
            return False

        amt = Decimal(str(getattr(self, "amount_laundry_partner", 0) or 0))
        if amt <= 0:
            return False

        amt = amt.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        # get/create wallet blanchisserie
        wallet, _ = Wallet.objects.get_or_create(
            owner_type="laundry",
            laundry_partner_id=laundry_partner_id,
            currency="XOF",
            defaults={},
        )

        # idempotence: already traced
        if WalletTransaction.objects.filter(
            order=self,
            wallet=wallet,
            direction="in",
            type__in=("credit", "payout"),
            amount=amt,
        ).exists():
            return True
        with transaction.atomic():
            wallet.balance = (wallet.balance + amt).quantize(Decimal("0.01"))
            wallet.save(update_fields=["balance", "updated_at"])

            if hasattr(WalletTransaction, "create_tx"):
                WalletTransaction.create_tx(
                    wallet=wallet,
                    order=self,
                    leg=None,
                    type="credit",
                    direction="in",
                    amount=amt,
                    description=f"ORDER#{self.pk} payout blanchisserie (repair)",
                    allow_orphan=False,
                )
            else:
                WalletTransaction.objects.create(
                    wallet=wallet,
                    order=self,
                    leg=None,
                    type="credit",
                    direction="in",
                    amount=amt,
                    description=f"ORDER#{self.pk} payout blanchisserie (repair)",
                )

        return True


    def mark_as_paid_and_distribute(self):
        if getattr(self, "payment_status", None) != "paid":
            return

        with transaction.atomic():
            # 🔒 lock DB pour éviter double distribution en concurrence
            type(self).objects.select_for_update().filter(pk=self.pk).values('id').first()
            self.refresh_from_db(fields=['wallets_distributed'])

        # 1) Revenus (une seule fois)
        if not getattr(self, "wallets_distributed", False):
            # ✅ Si les tx existent déjà, on (re)verrouille wallets_distributed et on évite toute redist.
            try:
                from wallets.models import WalletTransaction
                already = WalletTransaction.objects.filter(
                    order=self,
                    direction="in",
                    type="credit",
                    wallet__owner_type__in=("laundry", "internal"),
                    leg__isnull=True,
                ).exists()
                if already:
                    type(self).objects.filter(pk=self.pk).update(wallets_distributed=True)
                    self.wallets_distributed = True
                    # on ne tente PAS distribute_order_revenues (évite tout risque)
                    pass
                else:
                    from wallets.services import distribute_order_revenues
                    distribute_order_revenues(self)
                    if self._has_partner_distribution():
                        type(self).objects.filter(pk=self.pk).update(wallets_distributed=True)
                        self.wallets_distributed = True
            except Exception:
                import os
                if os.environ.get("PAYOUT_DEBUG") == "1":
                    raise
                pass


        # 1.b) MLM (doit pouvoir se jouer même si wallets_distributed=True)
        if not getattr(self, "mlm_distributed", False):
            try:
                from mlm.services import generate_mlm_commissions_for_order
                generate_mlm_commissions_for_order(self)
                type(self).objects.filter(pk=self.pk).update(mlm_distributed=True)
                self.mlm_distributed = True
            except Exception as e:
                import os
                if os.environ.get("PAYOUT_DEBUG") == "1":
                    raise
                # log soft en console serveur (utile en dev)
                try:
                    print(f"[MLM] Order {self.id} failed: {e}")
                except Exception:
                    pass

        # 2) Payout legs DONE (idempotent)  ✅ TOUJOURS exécuter même si wallets_distributed=True
        from orders.service_layer.payouts import trigger_driver_payout_for_leg

        # ✅ Important: ne pas filtrer sur driver__isnull=False, le trigger gère le fallback sur order.delivery_partner
        legs_done = self.legs.filter(status="done").select_related("driver").order_by("id")
        for leg in legs_done:
            trigger_driver_payout_for_leg(leg)

        # 3) driver_wallet_credited = True uniquement si le livreur a reçu 100% du montant attendu
        # ✅ basé UNIQUEMENT sur les payouts des legs DONE (réversible si un leg repasse pending/canceled)
        try:
            from decimal import Decimal
            from django.db.models import Sum
            from wallets.models import WalletTransaction

            target = Decimal(str(getattr(self, "amount_driver_partner", 0) or 0))

            # total payouts driver UNIQUEMENT sur les jambes "done"
            total_paid_done = WalletTransaction.objects.filter(
                order_id=self.id,
                wallet__owner_type="driver",
                type="payout",
                direction="in",
                leg__isnull=False,
                leg__status="done",
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            # si target>0: credited seulement si total_paid_done >= target
            # si target==0: credited si au moins 1 payout done existe
            should = (total_paid_done > 0) if (target <= 0) else (total_paid_done >= target)

            if bool(getattr(self, "driver_wallet_credited", False)) != bool(should):
                type(self).objects.filter(pk=self.pk).update(driver_wallet_credited=should)
                self.driver_wallet_credited = should

        except Exception:
            # pas critique pour le flux
            pass

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
        laundry_tx = (first_tx(owner_type="laundry", tx_type="payout", direction="in") or first_tx(owner_type="laundry", tx_type="credit", direction="in"))
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

    from django.db import IntegrityError
    import secrets
    import string

    def _gen_order_code():
        # 3 lettres + 11 chars = <= 20 (ton max_length=20)
        alphabet = string.ascii_uppercase + string.digits
        return "ODR" + "".join(secrets.choice(alphabet) for _ in range(11))

    def save(self, *args, **kwargs):
        from decimal import Decimal
        from django.utils import timezone
        from django.apps import apps
        from django.core.exceptions import ValidationError
        from django.db import IntegrityError
        import secrets, string

        payment_just_paid = False
        status_just_done = False


        # ============================================================
        # ✅ CODE UNIQUE (uniquement si vide) + anti-collision + retry insert
        # ============================================================
        def gen_code():
            alphabet = string.ascii_uppercase + string.digits
            return "ODR" + "".join(secrets.choice(alphabet) for _ in range(11))

        if not (self.code or "").strip():
            # 1) essaie quelques fois sans toucher au save() existant
            for _ in range(8):
                candidate = gen_code()
                if not Order.objects.filter(code=candidate).exists():
                    self.code = candidate
                    break
            # fallback (au cas où)
            if not (self.code or "").strip():
                self.code = gen_code()

        # ... ensuite ton code existant (old/payment locks/etc)
        # IMPORTANT: on ne doit JAMAIS régénérer si self.code est déjà rempli.

        old = None
        if self.pk:
            old = Order.objects.filter(pk=self.pk).first()

            # ============================================================
            # 🔒 GARDE-FOU (AVANT ÉCRITURE) — Interdire paid -> non-paid si tx wallet
            # ============================================================
            if old and getattr(old, "payment_status", None) == "paid" and getattr(self, "payment_status", None) != "paid":
                WalletTransaction = apps.get_model("wallets", "WalletTransaction")
                if WalletTransaction.objects.filter(order_id=self.pk).exists():
                    raise ValidationError({"payment_status": "Impossible de repasser de PAID à non-paid : des transactions wallet existent déjà."})

            # flags transitions
            if old:
                if old.payment_status != "paid" and self.payment_status == "paid":
                    payment_just_paid = True
                if old.status != "done" and self.status == "done":
                    status_just_done = True

        # ✅ S'assurer que les montants (dont total_client_ttc) sont à jour
        # MAIS: ne pas écraser un total_client_ttc déjà fourni manuellement (tests / imports)
        # quand il n'y a pas encore de lignes (items).
        try:
            from decimal import Decimal
            total_existing = Decimal(str(getattr(self, "total_client_ttc", 0) or 0))
            has_items = False
            try:
                # évite une query coûteuse si relation non prête
                has_items = bool(getattr(self, "items", None) and self.items.exists())
            except Exception:
                has_items = False

            if not (total_existing > 0 and not has_items):
                self.update_financials(save=False)
        except Exception:
            pass

        # ============================================================
        # 🔒 VERROUS PAIEMENT (AVANT ÉCRITURE)
        #  - paid => amount_paid=total_client_ttc + payment_date non null
        #  - unpaid => amount_paid=0 + payment_date=None
        #  - amount_paid borné [0, total_client_ttc]
        # ============================================================
        try:
            st = (getattr(self, "payment_status", "") or "unpaid").strip().lower()

            total_ttc = Decimal(str(getattr(self, "total_client_ttc", 0) or 0))
            # ✅ fallback : si update_financials a ramené 0 mais qu'on avait un total existant en DB
            if total_ttc <= 0 and old is not None:
                try:
                    total_ttc = Decimal(str(getattr(old, "total_client_ttc", 0) or 0))
                except Exception:
                    pass
            amt = Decimal(str(getattr(self, "amount_paid", 0) or 0))

            # borne basse
            if amt < 0:
                raise ValidationError("amount_paid ne peut pas être négatif.")

            # cap haute
            if total_ttc > 0 and amt > total_ttc:
                self.amount_paid = total_ttc
                amt = total_ttc

            if st == "paid":
                # ❌ Interdire la TRANSITION vers PAID si total_client_ttc est à 0
                # (mais ne pas bloquer les éditions d'un ordre déjà "paid" hérité / mal calculé)
                if total_ttc <= 0 and payment_just_paid:
                    raise ValidationError({"payment_status": "Impossible de marquer PAID : total_client_ttc est à 0."})

                # paid => amount_paid doit être total_ttc
                if total_ttc > 0 and amt < total_ttc:
                    self.amount_paid = total_ttc

                # paid => payment_date obligatoire
                if getattr(self, "payment_date", None) is None:
                    self.payment_date = timezone.now()

            elif st == "unpaid":
                # unpaid => aucun montant payé ni date
                if amt != 0:
                    self.amount_paid = Decimal("0")
                if getattr(self, "payment_date", None) is not None:
                    self.payment_date = None

            elif st == "partial":
                # cohérence simple : partial ne peut pas être soldé
                if total_ttc > 0 and amt >= total_ttc:
                    self.payment_status = "paid"
                    if getattr(self, "payment_date", None) is None:
                        self.payment_date = timezone.now()
                elif amt <= 0:
                    self.payment_status = "unpaid"
                    self.amount_paid = Decimal("0")
                    self.payment_date = None
                else:
                    # si partial avec montant >0 et pas de date, on peut dater
                    if getattr(self, "payment_date", None) is None:
                        self.payment_date = timezone.now()

        except ValidationError:
            raise
        except Exception:
            # ne pas casser une sauvegarde pour une conversion; on laisse le reste gérer
            pass

        old_status_db = None
        try:
            if self.pk:
                old_status_db = type(self).objects.filter(pk=self.pk).values_list("payment_status", flat=True).first()
        except Exception:
            old_status_db = None

        # ✅ Écriture DB (avec retry si collision code)
        try:
            # ✅ Si save(update_fields=...) ne contient pas les montants, ils ne seront pas persistés.
            # Donc en cas d'annulation, on élargit update_fields pour inclure les champs neutralisés.
            try:
                if getattr(self, "status", None) == "canceled":
                    uf = kwargs.get("update_fields", None)
                    if uf is not None:
                        uf = set(list(uf))
                        for f in ("client_fee_share", "driver_amount", "fagni_margin"):
                            if hasattr(self, f):
                                uf.add(f)
                        kwargs["update_fields"] = list(uf)
            except Exception:
                pass

            # ✅ Jambe annulée => neutraliser montants (source-of-truth DB)
            # Peu importe d'où vient l'annulation (views, admin, scripts, normalizers)
            try:
                from decimal import Decimal
                if getattr(self, "status", None) == "canceled":
                    if hasattr(self, "client_fee_share"):
                        self.client_fee_share = Decimal("0")
                    if hasattr(self, "driver_amount"):
                        self.driver_amount = Decimal("0")
                    if hasattr(self, "fagni_margin"):
                        self.fagni_margin = Decimal("0")
            except Exception:
                pass

            super().save(*args, **kwargs)
        except IntegrityError as e:
            # Collision code unique (rare mais possible)
            if "orders_order.code" in str(e) or "UNIQUE constraint failed: orders_order.code" in str(e):
                if not (self.code or "").strip():
                    self.code = None
                # regen + retry 1 fois
                import secrets, string
                alphabet = string.ascii_uppercase + string.digits
                self.code = "ODR" + "".join(secrets.choice(alphabet) for _ in range(11))
                super().save(*args, **kwargs)
            else:
                raise
        # ============================================================
        # ✅ RESYNC CANONIQUE depuis Payment (SOURCE DE VÉRITÉ)
        # Objectif:
        # - éviter amount_paid "gonflé" si total_client_ttc bouge après coup
        # - recalculer paid/partial/unpaid depuis la somme des paiements
        # - respecter le verrou wallet: ne pas downgrader PAID si tx wallet existent
        # ============================================================
        # ✅ Assurer que total_client_ttc est à jour avant de recalculer paid/partial/unpaid
        try:
            self.update_financials(save=False)
        except Exception:
            pass

        try:
            Payment = apps.get_model("orders", "Payment")
            if self.pk and Payment.objects.filter(order_id=self.pk).exists():
                from django.db.models import Sum

                total_ttc = Decimal(str(getattr(self, "total_client_ttc", 0) or 0))
                paid_sum = Payment.objects.filter(order_id=self.pk).aggregate(s=Sum("amount")).get("s") or 0
                paid_sum = Decimal(str(paid_sum))

                if paid_sum < 0:
                    paid_sum = Decimal("0")

                # total invalide => jamais paid
                if total_ttc <= 0:
                    new_status = "unpaid"
                    effective_paid = Decimal("0")
                else:
                    effective_paid = min(paid_sum, total_ttc)
                    if paid_sum <= 0:
                        new_status = "unpaid"
                    elif paid_sum < total_ttc:
                        new_status = "partial"
                    else:
                        new_status = "paid"

                # 🔒 si déjà PAID et des tx wallet existent => on ne downgrade pas
                old_status_db = (getattr(old, "payment_status", None) if old else None) or getattr(self, "payment_status", None) or "unpaid"
                if old_status_db == "paid" and new_status != "paid":
                    WalletTransaction = apps.get_model("wallets", "WalletTransaction")
                    if WalletTransaction.objects.filter(order_id=self.pk).exists():
                        new_status = "paid"
                        # on garde amount_paid tel quel (ou cap au total)
                        effective_paid = Decimal(str(getattr(self, "amount_paid", 0) or 0))
                        if total_ttc > 0 and effective_paid > total_ttc:
                            effective_paid = total_ttc

                updates = {}
                if getattr(self, "payment_status", None) != new_status:
                    updates["payment_status"] = new_status
                    self.payment_status = new_status

                # amount_paid toujours calé sur la vérité
                if Decimal(str(getattr(self, "amount_paid", 0) or 0)) != effective_paid:
                    updates["amount_paid"] = effective_paid
                    self.amount_paid = effective_paid

                # dates/facture si on devient paid
                if new_status == "paid":
                    if getattr(self, "payment_date", None) is None:
                        updates["payment_date"] = timezone.now()
                        self.payment_date = updates["payment_date"]
                    if getattr(self, "invoice_date", None) is None:
                        updates["invoice_date"] = self.payment_date or timezone.now()
                        self.invoice_date = updates["invoice_date"]
                    inv_st = getattr(self, "invoice_status", None)
                    if inv_st in (None, "", "draft", "issued"):
                        updates["invoice_status"] = "paid"
                        self.invoice_status = "paid"

                # si unpaid => optionnel : effacer payment_date (mais tu avais déjà ce verrou avant)
                if new_status == "unpaid":
                    # on ne force pas, mais si tu veux strict:
                    # updates["payment_date"] = None; self.payment_date = None
                    pass

                if updates:
                    Order.objects.filter(pk=self.pk).update(**updates)

        except Exception:
            pass

        # ============================================================
        # ✅ AUTO-REPAIR: si la commande devient PAID via RESYNC Payment
        # - sync legs (driver_id + driver_amount)
        # - payout immédiat pour les legs déjà "done"
        # Cas réel: leg terminé AVANT le paiement -> payout doit partir à l'instant où ça devient PAID.
        # ============================================================
        try:
            prev_status = (old_status_db or (getattr(old, "payment_status", None) if old else None) or "unpaid")
            became_paid_via_resync = (prev_status != "paid" and getattr(self, "payment_status", None) == "paid")

            if became_paid_via_resync and self.pk:
                from orders.models import sync_delivery_legs_for_order, DeliveryLeg
                from orders.service_layer.payouts import trigger_driver_payout_for_leg

                # 1) sync legs (remplit driver + montants par jambe)
                try:
                    sync_delivery_legs_for_order(self)
                except Exception:
                    pass

                # 2) payout pour tous les legs déjà done (idempotent)
                done_legs = (
                    DeliveryLeg.objects
                    .filter(order_id=self.pk, status="done")
                    .select_related("order", "driver")
                    .order_by("id")
                )
                for leg in done_legs:
                    try:
                        trigger_driver_payout_for_leg(leg)
                    except Exception:
                        pass

        except Exception:
            pass

        # 2) Paiement livreur (DÉSACTIVÉ : payout driver géré par legs)
        # payout driver géré par legs (orders.service_layer.payouts.trigger_driver_payout_for_leg)
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
        constraints = [
            models.UniqueConstraint(fields=["order", "leg_type"], name="uniq_leg_per_order_type"),
        ]

    def __str__(self):
        return f"{self.get_leg_type_display()} - {self.order.code} - {self.driver}"


    def save(self, *args, **kwargs):
        """
        Auto-trigger payout driver quand une jambe passe à DONE.

        🔒 Verrouillage strict :
        - Si un payout existe (WalletTransaction type='payout', direction='in' sur ce leg),
          alors:
            - status forcé à 'done'
            - driver_amount forcé à tx.amount (source de vérité)
            - client_fee_share + fagni_margin figés (repris depuis la DB)
            - update_fields enrichi pour éviter qu’un save(update_fields=['status']) “drop” les champs verrouillés

        Autres règles :
        - Un leg ne peut pas passer assigned/in_progress/done sans driver (sauf si payout existe → historique)
        - canceled ne peut pas être réouvert
        - canceled => montants neutralisés (0)
        - timestamps auto sur in_progress / done
        """
        from django.utils import timezone

        # snapshot DB (si exists)
        old = None
        old_status = None
        if self.pk:
            try:
                old = DeliveryLeg.objects.filter(pk=self.pk).values(
                    "status", "client_fee_share", "fagni_margin", "driver_amount"
                ).first()
                old_status = (old.get("status") if old else None)
            except Exception:
                old = None
                old_status = None

        # Detect payout tx (lock)
        tx = None
        if self.pk:
            try:
                from wallets.models import WalletTransaction
                tx = (
                    WalletTransaction.objects.filter(
                        order_id=self.order_id,
                        leg_id=self.pk,
                        type="payout",
                        direction="in",
                    )
                    .order_by("-id")
                    .first()
                )
            except Exception:
                tx = None

        has_payout = bool(tx)

        # 🔒 LOCK payout : status + montants
        if has_payout:
            # status toujours DONE
            if (self.status or "").lower() != "done":
                self.status = "done"

            # driver_amount = tx.amount (source-of-truth)
            try:
                self.driver_amount = tx.amount
            except Exception:
                pass

            # figer les autres montants sur la DB (ne plus bouger après paiement)
            if old:
                try:
                    self.client_fee_share = old.get("client_fee_share")
                except Exception:
                    pass
                try:
                    self.fagni_margin = old.get("fagni_margin")
                except Exception:
                    pass

            # forcer update_fields à inclure les champs verrouillés
            uf = kwargs.get("update_fields", None)
            if uf is not None:
                uf = set(list(uf))
                uf.update({"status", "driver_amount", "client_fee_share", "fagni_margin"})
                kwargs["update_fields"] = list(uf)

        # 🔒 Guard: sans driver, pas de assigned/in_progress/done (sauf si payout lock)
        try:
            if (not has_payout) and self.driver_id is None and (self.status or "").lower() in ("assigned", "in_progress", "done"):
                self.status = "pending"
        except Exception:
            pass

        # 🔒 Freeze: canceled ne peut pas être réouvert
        try:
            if old_status == "canceled" and self.status != "canceled":
                self.status = "canceled"
        except Exception:
            pass

        # ✅ canceled => neutraliser montants + garantir persistance même avec update_fields=["status"]
        try:
            if (getattr(self, "status", None) or "").lower() == "canceled":
                from decimal import Decimal
                self.client_fee_share = Decimal("0")
                self.driver_amount = Decimal("0")
                self.fagni_margin = Decimal("0")

                uf = kwargs.get("update_fields", None)
                if uf is not None:
                    uf = set(list(uf))
                    uf.update({"status", "client_fee_share", "driver_amount", "fagni_margin"})
                    kwargs["update_fields"] = list(uf)
        except Exception:
            pass

        # timestamps
        if self.status == "in_progress" and not self.started_at:
            self.started_at = timezone.now()
        if self.status == "done" and not self.finished_at:
            self.finished_at = timezone.now()

        super().save(*args, **kwargs)

        # trigger payout uniquement si transition vers done (idempotent côté service)
        if (old_status != "done") and (self.status == "done"):
            try:
                from orders.service_layer.payouts import trigger_driver_payout_for_leg
                trigger_driver_payout_for_leg(self)
            except Exception:
                pass


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


# --- MVP pressing: preuves & pesée ---

class OrderEvidencePhoto(models.Model):
    ACTOR_CHOICES = [
        ("client", "Client"),
        ("driver", "Livreur"),
        ("laundry", "Blanchisserie"),
        ("ops", "Ops/FAGNI"),
    ]

    KIND_CHOICES = [
        ("pickup_items", "Articles au départ (preuve)"),
        ("bag_before_seal", "Sac avant scellage"),
        ("bag_sealed", "Sac scellé"),
        ("weighing_scale", "Photo balance (pesée)"),
        ("dropoff_to_laundry", "Dépôt chez blanchisseur"),
        ("return_from_laundry", "Retour depuis blanchisseur"),
        ("delivery_to_client", "Livraison au client"),
        ("issue", "Litige / anomalie"),
    ]

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="evidence_photos")
    leg = models.ForeignKey("orders.DeliveryLeg", on_delete=models.SET_NULL, null=True, blank=True, related_name="evidence_photos")

    actor_type = models.CharField(max_length=16, choices=ACTOR_CHOICES)
    actor_id = models.PositiveIntegerField(null=True, blank=True)  # driver_id ou laundry_partner_id (simple)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)

    image = models.ImageField(upload_to="orders/evidence/", verbose_name="Photo")
    caption = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["order", "kind", "created_at"]),
            models.Index(fields=["actor_type", "created_at"]),
        ]

    def __str__(self):
        return f"Evidence #{self.id} {self.kind} order={self.order_id}"


class OrderWeighing(models.Model):
    STATUS_CHOICES = [
        ("draft", "Brouillon"),
        ("confirmed", "Confirmée"),
        ("disputed", "Contestée"),
        ("resolved", "Résolue OPS"),
    ]

    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="weighing")
    weight_kg = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    scale_photo = models.ImageField(upload_to="orders/weighing/", null=True, blank=True)

    entered_by_type = models.CharField(max_length=16, choices=[("driver","Livreur"),("laundry","Blanchisserie"),("ops","Ops/FAGNI")])
    entered_by_id = models.PositiveIntegerField(null=True, blank=True)

    confirmed_by_other = models.BooleanField(default=False)
    confirmed_by_type = models.CharField(max_length=16, blank=True, default="")
    confirmed_by_id = models.PositiveIntegerField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="draft")
    notes = models.CharField(max_length=250, blank=True, default="")
    # --- Résolution OPS (litige pesée) ---
    final_weight_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_weighings",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.CharField(max_length=250, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Weighing order={self.order_id} {self.weight_kg}kg {self.status}"



# --- MVP pressing: avis client (rating) ---
class OrderRating(models.Model):
    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="rating")

    # 1 à 5 étoiles
    score = models.PositiveSmallIntegerField(default=0)
    comment = models.CharField(max_length=500, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["score", "created_at"]),
        ]

    def __str__(self):
        return f"Rating order={self.order_id} score={self.score}"


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

    def save(self, *args, **kwargs):
        """
        À chaque paiement enregistré (création):
        - Refuse si total_client_ttc == 0
        - Refuse si commande déjà soldée
        - Refuse si surpaiement > reste dû (paiement direct)
        - Puis sync order.amount_paid + order.payment_status (+ payment_date)
        - Si devient PAID => mark_as_paid_and_distribute (idempotent)
        """
        from decimal import Decimal
        from django.core.exceptions import ValidationError
        from django.db.models import Sum
        from django.utils import timezone

        is_new = self.pk is None

        # ====== GUARDS AVANT INSERT (création uniquement) ======
        if is_new:
            o = self.order

            # total TTC depuis DB (source stable)
            total_ttc_db = type(o).objects.filter(pk=o.pk).values_list("total_client_ttc", flat=True).first()
            total_ttc = Decimal(str(total_ttc_db or 0))

            if total_ttc <= 0:
                raise ValidationError("Impossible d'enregistrer un paiement : total_client_ttc est à 0.")

            # somme payée actuelle (sans ce paiement)
            paid_sum = type(self).objects.filter(order=o).aggregate(s=Sum("amount")).get("s") or 0
            paid_sum = Decimal(str(paid_sum))
            if paid_sum < 0:
                paid_sum = Decimal("0")

            remaining = total_ttc - paid_sum
            if remaining <= 0:
                raise ValidationError("Paiement refusé : commande déjà soldée.")

            amt = Decimal(str(getattr(self, "amount", 0) or 0))
            if amt <= 0:
                raise ValidationError("Paiement refusé : montant invalide.")

            if amt > remaining:
                raise ValidationError("Paiement refusé : montant supérieur au reste dû.")

        # ====== INSERT ======
        super().save(*args, **kwargs)

        # On ne recalcule que sur création (sinon boucles)
        if not is_new:
            return

        o = self.order

        # total TTC depuis DB
        total_ttc_db = type(o).objects.filter(pk=o.pk).values_list("total_client_ttc", flat=True).first()
        total_ttc = Decimal(str(total_ttc_db or 0))

        paid_sum = type(self).objects.filter(order=o).aggregate(s=Sum("amount")).get("s") or 0
        paid_sum = Decimal(str(paid_sum))
        if paid_sum < 0:
            paid_sum = Decimal("0")

        if total_ttc <= 0:
            new_status = "unpaid"
            effective_paid = Decimal("0")
        else:
            effective_paid = min(paid_sum, total_ttc)
            if paid_sum <= 0:
                new_status = "unpaid"
            elif paid_sum < total_ttc:
                new_status = "partial"
            else:
                new_status = "paid"

        old_status_db = type(o).objects.filter(pk=o.pk).values_list("payment_status", flat=True).first() or "unpaid"
        becomes_paid = (old_status_db != "paid" and new_status == "paid")

        updates = {"amount_paid": effective_paid}
        if old_status_db != new_status:
            updates["payment_status"] = new_status

        if new_status == "paid":
            if not type(o).objects.filter(pk=o.pk, payment_date__isnull=False).exists():
                updates["payment_date"] = timezone.now()

        if updates:
            type(o).objects.filter(pk=o.pk).update(**updates)

        if becomes_paid:
            o2 = type(o).objects.get(pk=o.pk)
            o2.mark_as_paid_and_distribute()
