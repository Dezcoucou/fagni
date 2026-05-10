from decimal import Decimal, ROUND_HALF_UP

from django.db import models
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum


class ReferralLink(models.Model):
    """
    Profil de parrainage MLM FAGNI.
    Un profil est en général rattaché à un client FAGNI.
    """

    ACTOR_CHOICES = [
        ("client", "Client particulier"),
        ("influencer", "Influenceur / apporteur d'affaires"),
        ("corporate", "Entreprise / partenaire"),
    ]

    # Client FAGNI rattaché (facultatif si un jour on ouvre à d'autres acteurs)
    customer = models.ForeignKey(
        "orders.Customer",
        on_delete=models.CASCADE,
        related_name="referral_profiles",
        verbose_name="Client FAGNI",
        null=True,
        blank=True,
    )

    # Code de parrainage unique (ex : PARRAIN1, CODE123, etc.)
    referral_code = models.CharField(
        "Code affilié",
        max_length=50,
        unique=True,
        verbose_name="Code parrainage"
    )

    # Parrain (upline) sur 1 niveau
    sponsor = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="direct_referrals",
        verbose_name="Parrain",
    )

    actor_type = models.CharField(
        "Type d'acteur",
        max_length=20,
        choices=ACTOR_CHOICES,
        default="client",
    )

    created_at = models.DateTimeField("Créé le", auto_now_add=True)

    class Meta:
        verbose_name = "Profil de parrainage"
        verbose_name_plural = "Profils de parrainage"
        ordering = ["-created_at"]

    def __str__(self):
        label = self.referral_code or "Profil MLM"
        if self.customer:
            return f"{label} - {self.customer.name}"
        return label

    # ---------- Upline sur 3 niveaux ----------
    def get_upline(self, levels: int = 3):
        """
        Remonte la chaîne de parrainage jusqu'à N niveaux max.
        Retourne une liste [N1, N2, N3] (moins si la chaîne est plus courte).
        """
        upline = []
        current = self.sponsor
        depth = 0
        while current and depth < levels:
            upline.append(current)
            current = current.sponsor
            depth += 1
        return upline


class ReferralCommission(models.Model):
    """
    Commission MLM générée pour un profil (bénéficiaire) sur une commande donnée.
    On stocke :
    - le niveau (1, 2 ou 3)
    - la base de calcul (service_fee_base)
    - le pourcentage
    - le montant de la commission
    """

    beneficiary_profile = models.ForeignKey(
        ReferralLink,
        on_delete=models.CASCADE,
        related_name="commissions",
        verbose_name="Bénéficiaire",
    )

    level = models.PositiveSmallIntegerField(
        "Niveau",
        help_text="1 = filleul direct, 2 = 2e niveau, 3 = 3e niveau",
    )

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="mlm_commissions",
        verbose_name="Commande",
    )

    service_fee_base = models.IntegerField(
        "Base service fee (FCFA)",
        help_text="Montant du service FAGNI utilisé comme base de calcul.",
    )

    commission_percent = models.DecimalField(
        "Pourcentage appliqué",
        max_digits=5,
        decimal_places=2,
        help_text="Ex : 5.00 pour 5 %",
    )

    commission_amount = models.IntegerField(
        "Montant de la commission (FCFA)",
    )

    created_at = models.DateTimeField("Créée le", auto_now_add=True)

    class Meta:
        verbose_name = "Commission MLM"
        verbose_name_plural = "Commissions MLM"
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"N{self.level} - {self.commission_amount} FCFA "
            f"({self.beneficiary_profile} - Cmd {self.order.code})"
        )


class WalletTransaction(models.Model):
    """
    Mouvement d'argent du wallet MLM.
    """

    TYPE_CHOICES = [
        ("mlm_commission", "Commission MLM"),
        ("payout", "Retrait / paiement"),
        ("adjustment", "Ajustement"),
    ]

    profile = models.ForeignKey(
        ReferralLink,
        on_delete=models.CASCADE,
        related_name="wallet_transactions",
        verbose_name="Profil de parrainage",
    )

    type = models.CharField(
        "Type de mouvement",
        max_length=20,
        choices=TYPE_CHOICES,
        verbose_name="Type"
    )

    amount = models.IntegerField(
        "Montant (FCFA)",
        help_text="Positif pour crédit, négatif pour débit.",
    )

    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mlm_wallet_transactions",
        verbose_name="Commande"
    )

    description = models.CharField(
        "Description",
        max_length=255,
        blank=True,
        verbose_name="Description"
    )

    created_at = models.DateTimeField("Créé le", auto_now_add=True)

    class Meta:
        verbose_name = "Transaction Wallet"
        verbose_name_plural = "Transactions Wallet"
        ordering = ["-created_at"]

    def __str__(self):
        sign = "+" if self.amount >= 0 else "-"
        return f"{sign}{abs(self.amount)} FCFA - {self.get_type_display()}"

    @property
    def amount_decimal(self) -> Decimal:
        """
        Version Decimal du montant, pour des agrégations financières propres.
        """
        return Decimal(self.amount or 0).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )


class WithdrawalRequest(models.Model):
    """
    Demande de retrait du wallet affilié.
    """

    STATUS_CHOICES = [
        ("pending", "En attente"),
        ("approved", "Approuvée"),
        ("rejected", "Rejetée"),
        ("paid", "Payée"),
    ]

    profile = models.ForeignKey(
        ReferralLink,
        on_delete=models.CASCADE,
        related_name="withdrawal_requests",
        verbose_name="Profil affilié",
    )
    amount = models.PositiveIntegerField(
        "Montant demandé (FCFA)"
    )

    status = models.CharField(
        "Statut",
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        verbose_name="Statut"
    )

    payout_method = models.CharField(
        "Moyen de paiement",
        max_length=50,
        blank=True,
        help_text="Ex : Mobile Money, virement, espèce…",
    )

    payout_reference = models.CharField(
        "Référence de paiement",
        max_length=100,
        blank=True,
        help_text="Référence de transaction / reçu…",
    )

    created_at = models.DateTimeField("Créée le", auto_now_add=True)
    processed_at = models.DateTimeField("Traitée le", null=True, blank=True)

    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Traité par",
    )

    notes = models.TextField("Notes internes", blank=True)

    class Meta:
        verbose_name = "Demande de retrait"
        verbose_name_plural = "Demandes de retrait"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Retrait {self.amount} FCFA - {self.profile}"


class MLMSettings(models.Model):
    """
    Paramétrage global du plan MLM FAGNI.
    Permet d'ajuster les pourcentages et les règles métier
    sans toucher au code.
    """

    name = models.CharField(
        "Nom du paramétrage",
        max_length=100,
        default="Paramétrage par défaut",
        verbose_name="Nom"
    )

    # Est-ce que ce set de paramétrage est actif ?
    is_active = models.BooleanField(
        "Actif",
        default=True,
        verbose_name="Actif"
    )

    # Pourcentages de commissions (en %)
    n1_percent = models.DecimalField(
        "Pourcentage N1",
        max_digits=5,
        decimal_places=2,
        default=Decimal("10.00"),
        help_text="Commission niveau 1 (en % du service FAGNI).",
    )
    n2_percent = models.DecimalField(
        "Pourcentage N2",
        max_digits=5,
        decimal_places=2,
        default=Decimal("3.00"),
        help_text="Commission niveau 2 (en % du service FAGNI).",
    )
    n3_percent = models.DecimalField(
        "Pourcentage N3",
        max_digits=5,
        decimal_places=2,
        default=Decimal("2.00"),
        help_text="Commission niveau 3 (en % du service FAGNI).",
    )

    # Pour contrôle / info
    total_percent = models.DecimalField(
        "Pourcentage total théorique",
        max_digits=5,
        decimal_places=2,
        default=Decimal("15.00"),
        help_text="N1 + N2 + N3 (pour info / cohérence).",
    )

    # Seuil minimal pour générer des commissions MLM
    min_service_fee_for_commission = models.PositiveIntegerField(
        "Service fee minimum pour commissions (FCFA)",
        default=500,
        help_text=(
            "En-dessous de ce montant de service FAGNI, "
            "aucune commission MLM n'est générée."
        ),
    )

    # Paramètres de retrait
    min_withdrawal_amount = models.PositiveIntegerField(
        default=5000,
        verbose_name="Montant minimum de retrait (FCFA)",
    )
    max_daily_withdrawal = models.PositiveIntegerField(
        default=200000,
        verbose_name="Plafond de retrait par jour (FCFA)",
    )

    allow_negative_wallet = models.BooleanField(
        default=False,
        verbose_name="Autoriser un wallet négatif ?",
    )

    created_at = models.DateTimeField("Créé le", auto_now_add=True)
    updated_at = models.DateTimeField("Mis à jour le", auto_now=True)

    class Meta:
        verbose_name = "Paramétrage MLM"
        verbose_name_plural = "Paramétrages MLM"

    def __str__(self):
        return self.name or f"Paramétrage MLM #{self.pk}"

    # ---- Méthode utilitaire centrale ----
    @classmethod
    def get_active(cls):
        """
        Renvoie la configuration MLM active (ou la crée avec des valeurs par défaut).
        On ne met dans defaults QUE des champs qui existent vraiment sur le modèle.
        """
        obj, _created = cls.objects.get_or_create(
            is_active=True,
            defaults={
                "name": "Configuration globale MLM",
                "n1_percent": Decimal("10.00"),
                "n2_percent": Decimal("3.00"),
                "n3_percent": Decimal("2.00"),
                "total_percent": Decimal("15.00"),
                "min_service_fee_for_commission": 500,
                "min_withdrawal_amount": 5000,
                "max_daily_withdrawal": 200000,
                "allow_negative_wallet": False,
            },
        )
        return obj

    # ---------- Aliases de compatibilité ----------

    @property
    def enabled(self):
        """
        Alias de compatibilité : certains anciens codes/tests utilisent 'enabled'.
        On le mappe sur is_active.
        """
        return self.is_active

    @enabled.setter
    def enabled(self, value):
        self.is_active = bool(value)

    @property
    def level1_percent(self) -> Decimal:
        """
        Alias pour les anciens codes qui attendent level1_percent.
        """
        return self.n1_percent or Decimal("0.00")

    @property
    def level2_percent(self) -> Decimal:
        """
        Alias pour les anciens codes qui attendent level2_percent.
        """
        return self.n2_percent or Decimal("0.00")

    @property
    def level3_percent(self) -> Decimal:
        """
        Alias pour les anciens codes qui attendent level3_percent.
        """
        return self.n3_percent or Decimal("0.00")
