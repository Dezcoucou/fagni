from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.db import transaction
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from orders.models import Order, Customer, DeliveryLeg
from partners.models import LaundryPartner, DeliveryPartner, RelayPointPartner

User = get_user_model()


class Wallet(models.Model):
    """
    Wallet générique FAGNI :
    - côté client
    - côté blanchisserie
    - côté livreur
    - côté point relais
    - côté interne (FAGNI)
    """

    OWNER_TYPE_CHOICES = [
        ("customer", "Client FAGNI"),
        ("laundry", "Blanchisserie partenaire"),
        ("driver", "Livreur partenaire"),
        ("relay", "Point relais partenaire"),
        ("internal", "Interne FAGNI"),
    ]

    owner_type = models.CharField(
        "Type de propriétaire",
        max_length=20,
        choices=OWNER_TYPE_CHOICES,
    )

    # Liens possibles (UN seul doit être renseigné selon owner_type)
    customer = models.ForeignKey(
        Customer,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="wallets",
        verbose_name="Client",
    )

    laundry_partner = models.ForeignKey(
        LaundryPartner,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="wallets",
        verbose_name="Blanchisserie partenaire",
    )

    delivery_partner = models.ForeignKey(
        DeliveryPartner,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="wallets",
        verbose_name="Livreur partenaire",
    )

    relay_partner = models.ForeignKey(
        RelayPointPartner,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="wallets",
        verbose_name="Point relais partenaire",
    )

    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wallets",
        verbose_name="Utilisateur (interne)",
    )

    currency = models.CharField(
        "Devise",
        max_length=10,
        default="XOF",
    )

    balance = models.DecimalField(
        "Solde actuel",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    created_at = models.DateTimeField("Créé le", auto_now_add=True)
    updated_at = models.DateTimeField("Mis à jour le", auto_now=True)

    class Meta:
        verbose_name = "Wallet"
        verbose_name_plural = "Wallets"
        constraints = [
            # 1 wallet client / devise
            models.UniqueConstraint(
                fields=["customer", "currency"],
                condition=models.Q(owner_type="customer") & models.Q(customer__isnull=False),
                name="uniq_wallet_customer_currency",
            ),
            # 1 wallet blanchisserie / devise
            models.UniqueConstraint(
                fields=["laundry_partner", "currency"],
                condition=models.Q(owner_type="laundry") & models.Q(laundry_partner__isnull=False),
                name="uniq_wallet_laundry_currency",
            ),
            # 1 wallet point relais / devise
            models.UniqueConstraint(
                fields=["relay_partner", "currency"],
                condition=models.Q(owner_type="relay") & models.Q(relay_partner__isnull=False),
                name="uniq_wallet_relay_currency",
            ),
            # 1 wallet livreur / devise
            models.UniqueConstraint(
                fields=["delivery_partner", "currency"],
                condition=models.Q(owner_type="driver") & models.Q(delivery_partner__isnull=False),
                name="uniq_wallet_driver_currency",
            ),
            # 1 wallet interne (user) / devise (si tu veux un wallet par user interne)
            models.UniqueConstraint(
                fields=["user", "currency"],
                condition=models.Q(owner_type="internal") & models.Q(user__isnull=False),
                name="uniq_wallet_internal_currency",
            ),
            # 1 wallet plateforme (owner_type=internal, user NULL) / devise
            models.UniqueConstraint(
                fields=["currency"],
                condition=models.Q(owner_type="internal") & models.Q(user__isnull=True),
                name="uniq_wallet_internal_platform_currency",
            ),
        ]

    def clean(self):
        """
        Garde-fou : exactement 1 FK doit être renseigné (sauf internal plateforme),
        et il doit correspondre à owner_type.
        """
        super().clean()

        links = {
            "customer": self.customer_id,
            "laundry": self.laundry_partner_id,
            "driver": self.delivery_partner_id,
            "relay": self.relay_partner_id,
            "internal": self.user_id,
        }

        filled = [k for k, v in links.items() if v is not None]

        if self.owner_type == "internal":
            # autorise 0 (wallet plateforme) OU 1 (wallet d'un user interne)
            if len(filled) > 1:
                raise ValidationError("Wallet interne: renseigne au maximum un seul champ (user).")
            # Si un champ est rempli, il doit être "internal" (= user)
            if len(filled) == 1 and filled[0] != "internal":
                raise ValidationError("Wallet interne: seul le champ 'Utilisateur (interne)' peut être renseigné.")
            return

        # Tous les autres : exactement 1 champ rempli
        if len(filled) != 1:
            raise ValidationError("Un wallet doit avoir exactement 1 propriétaire (un seul champ lié).")

        expected = self.owner_type
        if filled[0] != expected:
            raise ValidationError(
                f"Incohérence: owner_type='{self.owner_type}' mais champ renseigné='{filled[0]}'."
            )

    def save(self, *args, **kwargs):
        # 🔒 WALLET BALANCE GUARD — interdit modification directe du solde hors services wallet
        if self.pk:
            try:
                old = Wallet.objects.filter(pk=self.pk).first()
                if old and old.balance != self.balance:
                    import inspect
                    allowed = any(
                        frame.function in ("credit_wallet", "debit_wallet", "apply_payout")
                        for frame in inspect.stack()
                    )
                    if not allowed:
                        import logging
                        logger = logging.getLogger("wallet_security")
                        logger.error(
                            "🚨 WALLET_FRAUD_BLOCKED | wallet_id=%s | old=%s | new=%s",
                            self.pk,
                            old.balance,
                            self.balance,
                        )
                        raise ValidationError({
                            "balance": "Modification wallet.balance interdite hors services wallet."
                        })
            except ValidationError:
                raise
            except Exception:
                pass

        super().save(*args, **kwargs)

    def __str__(self):
        label = f"{self.get_owner_type_display()} - {self.currency}"
        if self.customer:
            label = f"Client {self.customer.name} - {self.currency}"
        elif self.laundry_partner:
            label = f"Blanchisserie {self.laundry_partner.name} - {self.currency}"
        elif self.delivery_partner:
            label = f"Livreur {self.delivery_partner.name} - {self.currency}"
        elif self.relay_partner:
            label = f"Point relais {self.relay_partner.name} - {self.currency}"
        elif self.owner_type == "internal" and self.user:
            label = f"Interne {self.user.username} - {self.currency}"
        elif self.owner_type == "internal":
            label = f"Interne FAGNI - {self.currency}"
        return f"{label} (solde : {self.balance} {self.currency})"

    def credit(self, amount: Decimal, description: str = ""):
        """
        Crédite le wallet (entrée d'argent).
        """
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if amount <= 0:
            return

        self.balance = (self.balance + amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.save(update_fields=["balance", "updated_at"])

        WalletTransaction.create_tx(
            wallet=self,
            order=None,
            leg=None,
            type="credit",
            direction="in",
            amount=amount,
            description=description or "MANUAL: Crédit wallet",
            allow_orphan=True,
        )

    def debit(self, amount: Decimal, description: str = ""):
        """
        Débite le wallet (sortie d'argent).
        """
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if amount <= 0:
            return

        if (self.balance or Decimal("0.00")) < amount:
            # sécurité
            return

        self.balance = (self.balance - amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.save(update_fields=["balance", "updated_at"])

        WalletTransaction.create_tx(
            wallet=self,
            order=None,
            leg=None,
            type="debit",
            direction="out",
            amount=amount,
            description=description or "MANUAL: Débit wallet",
            allow_orphan=True,
        )

class WalletTransaction(models.Model):
    """
    Historique des mouvements d'un wallet.
    """

    class TxType(models.TextChoices):
        CREDIT = "credit", "Crédit"
        DEBIT = "debit", "Débit"
        PAYOUT = "payout", "Paiement / Retrait"
        REFUND = "refund", "Remboursement"
        ADJUSTMENT = "adjustment", "Ajustement"
        LEGACY_PAYOUT = "legacy_payout", "Legacy payout"

    class TxDirection(models.TextChoices):
        IN = "in", "Entrée"
        OUT = "out", "Sortie"

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name="Wallet",
    )

    order = models.ForeignKey(
        Order,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wallet_transactions",
        verbose_name="Commande",
    )

    leg = models.ForeignKey(
        DeliveryLeg,
        null=True,
        blank=True,
        on_delete=models.PROTECT,  # ✅ au lieu de SET_NULL
        related_name="wallet_transactions",
        verbose_name="Tronçon (leg)",
    )

    type = models.CharField(
        "Type",
        max_length=30,
        choices=TxType.choices,
    )

    direction = models.CharField(
        "Sens",
        max_length=10,
        choices=TxDirection.choices,
    )

    amount = models.DecimalField(
        "Montant",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    description = models.TextField("Description", blank=True)


    idempotency_key = models.CharField(
        "Clé idempotence",
        max_length=120,
        null=True,
        blank=True,
        db_index=True,
    )
    created_at = models.DateTimeField("Créée le", auto_now_add=True)


    class Meta:
        constraints = [
            # ✅ 0) Idempotence globale (si clé fournie)
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uniq_wallettx_idempotency_key",
            ),

            # ✅ 1) Unicité quand leg est NULL (cas internal / laundry / etc.)
            models.UniqueConstraint(
                fields=["wallet", "order", "type", "direction"],
                condition=models.Q(leg__isnull=True),
                name="uniq_wallet_order_type_dir_no_leg",
            ),

            # ✅ 2) Unicité par jambe quand leg est NOT NULL (cas payouts driver)
            models.UniqueConstraint(
                fields=["wallet", "order", "type", "direction", "leg"],
                condition=models.Q(leg__isnull=False),
                name="uniq_wallet_order_type_dir_leg",
            ),
        ]

    @classmethod
    def create_tx(
        cls,
        *,
        wallet,
        amount,
        direction: str,
        type: str,
        order=None,
        leg=None,
        description: str = "",
        idempotency_key: str | None = None,
        allow_orphan: bool = False,
    ):

        """
        Factory sécurisée pour créer une WalletTransaction.

        Règle HARD:
        - Interdit de créer une tx sans order ET sans leg
          sauf si allow_orphan=True.
        """
        if amount is None:
            raise ValueError("WalletTransaction.create_tx: amount is None")

        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if amount <= 0:
            return None

        if (order is None) and (leg is None) and (not allow_orphan):
            raise ValueError(
                "WalletTransaction.create_tx: orphan tx refused (order=None and leg=None). "
                "Use allow_orphan=True explicitly if intended."
            )

        if type == cls.TxType.LEGACY_PAYOUT:
            raise ValueError("WalletTransaction.create_tx: legacy_payout is forbidden (migration planned/handled).")

        return cls.objects.create(
            wallet=wallet,
            order=order,
            leg=leg,
            type=type,
            direction=direction,
            amount=amount,
              idempotency_key=idempotency_key,
            description=description or "",
        )

    def __str__(self):
        sign = "+" if self.direction == self.TxDirection.IN else "-"
        return f"{sign}{self.amount} {self.wallet.currency} – {self.type}"

    @property
    def signed_amount(self):
        if self.direction == self.TxDirection.OUT:
            return -self.amount
        return self.amount

    @property
    def label(self):
        return self.description

    @label.setter
    def label(self, value):
        self.description = value


class WithdrawalRequest(models.Model):
    """
    Demande de retrait sur un wallet (driver / laundry / customer / relay / internal).

    Règle :
    - Le débit réel du wallet + création de WalletTransaction se font UNIQUEMENT quand status passe à 'paid'.
    - Idempotent : si déjà traité (processed_at) ou si la tx existe déjà → on ne refait rien.
    """

    STATUS_CHOICES = [
        ("pending", "En attente"),
        ("approved", "Approuvée"),
        ("paid", "Payée"),
        ("rejected", "Rejetée"),
    ]

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="withdrawals",
        verbose_name="Wallet",
    )

    requested_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="withdrawals_requested",
        verbose_name="Demandé par (user)",
    )

    amount = models.DecimalField(
        "Montant demandé",
        max_digits=12,
        decimal_places=2,
    )

    status = models.CharField(
        "Statut",
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    created_at = models.DateTimeField("Créée le", auto_now_add=True)

    processed_at = models.DateTimeField("Traitée le", null=True, blank=True)
    processed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="withdrawals_processed",
        verbose_name="Traitée par",
    )

    admin_notes = models.TextField("Notes internes", blank=True)

    class Meta:
        verbose_name = "Demande de retrait"
        verbose_name_plural = "Demandes de retrait"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.get_beneficiary_display()} – {self.amount} {self.wallet.currency}"

    # ---------- HELPERS ----------
    def get_beneficiary_display(self) -> str:
        w = self.wallet
        if not w:
            return "—"
        # On affiche selon le owner_type et les FK existantes du Wallet
        if getattr(w, "delivery_partner_id", None):
            return f"Livraison: {getattr(w.delivery_partner, 'name', '—')}"
        if getattr(w, "laundry_partner_id", None):
            return f"Blanchisserie: {getattr(w.laundry_partner, 'name', '—')}"
        if getattr(w, "customer_id", None):
            return f"Client: {getattr(w.customer, 'name', '—')}"
        return f"{getattr(w, 'owner_type', 'wallet')} #{w.id}"

    def _tx_exists(self) -> bool:
        """
        Anti-doublon dur : si une transaction payout out existe déjà pour ce retrait.
        On s'appuie sur la description normalisée "Retrait #{id} – ...".
        """
        if not self.pk:
            return False

        needle = f"Retrait #{self.id}"
        return WalletTransaction.objects.filter(
            wallet_id=self.wallet_id,
            order__isnull=True,
            leg__isnull=True,
            type="payout",
            direction="out",
            description__icontains=needle,
        ).exists()

    def _can_debit_wallet(self) -> bool:
        if self.status != "paid":
            return False
        if self.processed_at is not None and self._tx_exists():
            return False
        amount = self.amount or Decimal("0.00")
        if amount <= 0:
            return False
        if self._tx_exists():
            return False
        return True

    def apply_payout(self):
        """
        Applique le retrait sur le wallet + crée la transaction (UNE SEULE FOIS).
        Tout est atomique + wallet verrouillé.
        """
        if not self._can_debit_wallet():
            return

        amount = (self.amount or Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        with transaction.atomic():
            # Recharger & verrouiller le wallet (anti course)
            wallet = Wallet.objects.select_for_update().get(pk=self.wallet_id)

            # Re-check sous lock
            if self._tx_exists():
                return
            if (wallet.balance or Decimal("0.00")) < amount:
                return

            # 1) Débit wallet
            wallet.balance = (wallet.balance - amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            wallet.save(update_fields=["balance", "updated_at"])

            # 2) Transaction
            WalletTransaction.objects.create(
                wallet=wallet,
                order=None,
                leg=None,
                type="payout",
                direction="out",
                amount=amount,
                description=f"Retrait #{self.id} – {self.get_beneficiary_display()}",
            )

            # 3) Marquer traité
            self.processed_at = timezone.now()
            self.save(update_fields=["processed_at"])
