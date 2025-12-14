from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.utils import timezone

from orders.models import Order, Customer
from partners.models import LaundryPartner, DeliveryPartner, RelayPointPartner

from django.contrib.auth import get_user_model

User = get_user_model()


class Wallet(models.Model):
    """
    Wallet générique FAGNI :
    - côté client
    - côté blanchisserie
    - côté livreur
    - côté interne (FAGNI)
    """

    OWNER_TYPE_CHOICES = [
        ("customer", "Client FAGNI"),
        ("laundry", "Blanchisserie partenaire"),
        ("driver", "Livreur partenaire"),
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

    def __str__(self):
        label = f"{self.get_owner_type_display()} - {self.currency}"
        if self.customer:
            label = f"Client {self.customer.name} - {self.currency}"
        elif self.laundry_partner:
            label = f"Blanchisserie {self.laundry_partner.name} - {self.currency}"
        elif self.delivery_partner:
            label = f"Livreur {self.delivery_partner.name} - {self.currency}"
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

        self.balance = (self.balance + amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.save(update_fields=["balance", "updated_at"])

        WalletTransaction.objects.create(
            wallet=self,
            order=None,
            type="credit",
            direction="in",
            amount=amount,
            description=description or "Crédit wallet",
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

        self.balance = (self.balance - amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.save(update_fields=["balance", "updated_at"])

        WalletTransaction.objects.create(
            wallet=self,
            order=None,
            type="debit",
            direction="out",
            amount=amount,
            description=description or "Débit wallet",
        )


class WalletTransaction(models.Model):
    """
    Mouvement sur un wallet :
    - topup / dépôt
    - paiement commande
    - commission MLM
    - ajustement manuel, etc.
    """

    TRANSACTION_TYPE_CHOICES = [
        ("topup", "Recharge / Dépôt"),
        ("payout", "Paiement sortant"),
        ("mlm_commission", "Commission MLM"),
        ("adjustment", "Ajustement manuel"),
        ("credit", "Crédit"),
        ("debit", "Débit"),
    ]

    DIRECTION_CHOICES = [
        ("in", "Entrant"),
        ("out", "Sortant"),
    ]

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name="Wallet",
    )

    # ⚠️ IMPORTANT : related_name différent de l'app mlm pour éviter le conflit
    order = models.ForeignKey(
        Order,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wallet_transactions_wallets",
        verbose_name="Commande liée",
    )

    type = models.CharField(
        "Type de transaction",
        max_length=30,
        choices=TRANSACTION_TYPE_CHOICES,
    )

    direction = models.CharField(
        "Sens",
        max_length=5,
        choices=DIRECTION_CHOICES,
    )

    amount = models.DecimalField(
        "Montant",
        max_digits=12,
        decimal_places=2,
    )

    description = models.CharField(
        "Description",
        max_length=255,
        blank=True,
    )

    created_at = models.DateTimeField("Créée le", auto_now_add=True)

    class Meta:
        verbose_name = "Transaction de wallet"
        verbose_name_plural = "Transactions de wallet"
        ordering = ["-created_at"]

    def __str__(self):
        sign = "+" if self.direction == "in" else "-"
        return f"{sign}{self.amount} {self.wallet.currency} – {self.type}"

    @property
    def signed_amount(self):
        """
        Montant signé (positif = entrant, négatif = sortant).
        """
        if self.direction == "out":
            return -self.amount
        return self.amount


class WithdrawalRequest(models.Model):
    """
    Demande de retrait d'un livreur sur son wallet.
    Le débit réel du wallet + transaction sont faits quand la demande passe en 'paid'.
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
        verbose_name="Wallet livreur",
    )

    delivery_partner = models.ForeignKey(
        DeliveryPartner,
        on_delete=models.CASCADE,
        related_name="withdrawals",
        verbose_name="Livreur",
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

    # Renseignés quand on traite la demande (approuvée / payée / rejetée)
    processed_at = models.DateTimeField(
        "Traitée le",
        null=True,
        blank=True,
    )
    processed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="withdrawals_processed",
        verbose_name="Traitée par",
    )

    admin_notes = models.TextField(
        "Notes internes",
        blank=True,
    )

    class Meta:
        verbose_name = "Demande de retrait livreur"
        verbose_name_plural = "Demandes de retrait livreur"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.delivery_partner} – {self.amount} {self.wallet.currency}"

    # ---------- LOGIQUE MÉTIER ----------

    def _can_debit_wallet(self) -> bool:
        """
        Sécurité : on ne débite que si :
        - montant > 0
        - statut = 'paid'
        - processed_at encore vide (pour éviter les doublons)
        - solde suffisant
        """
        if self.status != "paid":
            return False

        if self.processed_at is not None:
            # Déjà traité
            return False

        amount = self.amount or Decimal("0.00")
        if amount <= 0:
            return False

        if amount > (self.wallet.balance or Decimal("0.00")):
            # On bloque juste si pas assez de solde
            return False

        return True

    def apply_payout(self):
        """
        Applique le retrait sur le wallet + crée la transaction.
        Appelée automatiquement quand le statut passe à 'paid'.
        """
        if not self._can_debit_wallet():
            return

        amount = self.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 1) Débit du wallet
        self.wallet.balance = (self.wallet.balance - amount).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        self.wallet.save(update_fields=["balance", "updated_at"])

        # 2) Transaction associée
        WalletTransaction.objects.create(
            wallet=self.wallet,
            order=None,
            type="payout",
            direction="out",
            amount=amount,
            description=f"Retrait livreur {self.delivery_partner.name}",
        )

        # 3) Marquage comme traité
        self.processed_at = timezone.now()

    def save(self, *args, **kwargs):
        """
        On surveille le changement de statut.
        Si on vient de passer à 'paid', on applique le retrait.
        """
        old_status = None
        if self.pk:
            old_status = (
                type(self).objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )

        super().save(*args, **kwargs)

        # Si on vient de passer en 'paid', on applique le retrait une seule fois
        if old_status is not None and old_status != "paid" and self.status == "paid":
            refreshed = type(self).objects.get(pk=self.pk)
            refreshed.apply_payout()
            type(self).objects.filter(pk=self.pk).update(
                processed_at=refreshed.processed_at
            )

