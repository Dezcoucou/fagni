from django.db import models


class ReferralLink(models.Model):
    """
    Profil MLM pour un acteur FAGNI (client, blanchisserie, livreur).
    Chaque acteur a un code unique qui permet le parrainage.
    """

    ACTOR_TYPES = [
        ("customer", "Client"),
        ("laundry", "Blanchisserie"),
        ("delivery", "Livreur"),
    ]

    actor_type = models.CharField("Type d'acteur", max_length=20, choices=ACTOR_TYPES)

    customer = models.ForeignKey(
        'orders.Customer',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="referral_profile",
    )
    laundry_partner = models.ForeignKey(
        'partners.LaundryPartner',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="referral_profile",
    )
    delivery_partner = models.ForeignKey(
        'partners.DeliveryPartner',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="referral_profile",
    )

    referral_code = models.CharField(
        "Code de parrainage",
        max_length=20,
        unique=True,
    )

    sponsor = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="direct_referrals",
        help_text="Profil MLM qui a directement parrainé cet acteur",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Profil MLM"
        verbose_name_plural = "Profils MLM"

    def get_actor_name(self):
        """Renvoie le nom humain de l'acteur."""
        if self.customer:
            return self.customer.name
        if self.laundry_partner:
            return getattr(self.laundry_partner, "name", str(self.laundry_partner))
        if self.delivery_partner:
            return getattr(self.delivery_partner, "name", str(self.delivery_partner))
        return "—"

    def actor_label(self):
        """Renvoie un label lisible : Client - NOM / Livre…"""
        return f"{self.get_actor_type_display()} - {self.get_actor_name()}"

    def __str__(self):
        return self.actor_label()

    def get_upline(self, levels=3):
        """
        Retourne jusqu'à 3 niveaux de parrains.
        Exemple : [parrain1, parrain2, parrain3]
        """
        upline = []
        current = self

        for _ in range(levels):
            if current.parent:
                upline.append(current.parent)
                current = current.parent
            else:
                break

        return upline


    def get_upline(self, levels=3):
        """
        Retourne jusqu'à N niveaux de parrains.
        Exemple : [parrain_niveau_1, parrain_niveau_2, parrain_niveau_3]
        """
        upline = []
        current = self

        for _ in range(levels):
            if current.sponsor:
                upline.append(current.sponsor)
                current = current.sponsor
            else:
                break

        return upline



class ReferralCommission(models.Model):
    """
    Commission MLM générée sur une commande.
    """

    LEVEL_CHOICES = [
        (1, "Niveau 1"),
        (2, "Niveau 2"),
        (3, "Niveau 3"),
    ]

    beneficiary_profile = models.ForeignKey(
        ReferralLink,
        on_delete=models.CASCADE,
        related_name="commissions",
    )
    level = models.PositiveSmallIntegerField(choices=LEVEL_CHOICES)

    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.CASCADE,
        related_name="mlm_commissions",
    )

    service_fee_base = models.PositiveIntegerField()

    commission_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
    )

    commission_amount = models.PositiveIntegerField()

    created_at = models.DateTimeField(auto_now_add=True)
    is_paid = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Commission MLM"
        verbose_name_plural = "Commissions MLM"

    def __str__(self):
        return (
            f"L{self.level} - {self.commission_amount} FCFA "
            f"(commande {self.order_id})"
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
    )
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.IntegerField()

    order = models.ForeignKey(
        'orders.Order',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wallet_transactions",
    )

    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Transaction Wallet"
        verbose_name_plural = "Transactions Wallet"
        ordering = ["-created_at"]

    def __str__(self):
        sign = "+" if self.amount >= 0 else "-"
        return f"{sign}{abs(self.amount)} FCFA - {self.get_type_display()}"
