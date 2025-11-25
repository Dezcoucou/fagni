# mlm/services.py

from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from orders.models import Order
from .models import WalletTransaction, MLMConfig, ReferralLink


def get_referral_chain_for_customer(customer):
    """
    Retourne jusqu'à 3 niveaux de parrainage pour un client donné.
    On suppose :
      - ReferralLink(client=Customer, parent=ReferralLink ou None)
    """
    try:
        link = ReferralLink.objects.select_related("parent").get(customer=customer)
    except ReferralLink.DoesNotExist:
        return (None, None, None)

    n1 = link
    n2 = link.parent if link and link.parent_id else None
    n3 = n2.parent if n2 and n2.parent_id else None
    return (n1, n2, n3)


def has_already_generated_commissions(order: Order) -> bool:
    """
    Vérifie si cette commande a déjà engendré au moins
    une transaction wallet de type 'mlm_commission'.
    """
    return order.wallet_transactions.filter(type="mlm_commission").exists()


@transaction.atomic
def generate_mlm_commissions_for_order(order: Order) -> None:
    """
    Génère les commissions MLM pour une commande donnée si :
      - status = 'done'
      - service_fee > 0
      - la commande n'a pas déjà généré de commissions
      - le client a un parrainage
    """

    # Conditions minimales
    if order.status != "done":
        return

    service_fee = getattr(order, "service_fee", None)
    if not service_fee or service_fee <= 0:
        return

    if has_already_generated_commissions(order):
        return

    if not hasattr(order, "customer") or order.customer is None:
        return

    n1, n2, n3 = get_referral_chain_for_customer(order.customer)
    if not n1:
        # pas de parrain -> pas de MLM
        return

    cfg = MLMConfig.get_active()

    # taux en pourcentage -> décimaux
    l1_rate = (cfg.level1_rate or Decimal("0")) / Decimal("100")
    l2_rate = (cfg.level2_rate or Decimal("0")) / Decimal("100")
    l3_rate = (cfg.level3_rate or Decimal("0")) / Decimal("100")

    # conversions en FCFA entiers
    def compute_amount(rate: Decimal) -> int:
        if rate <= 0:
            return 0
        return int(service_fee * rate)

    amount_l1 = compute_amount(l1_rate)
    amount_l2 = compute_amount(l2_rate)
    amount_l3 = compute_amount(l3_rate)

    now = timezone.now()

    # niveau 1
    if n1 and amount_l1 > 0:
        WalletTransaction.objects.create(
            profile=n1,
            type="mlm_commission",
            amount=amount_l1,
            order=order,
            description=f"Commission N1 sur commande {order.code or order.id}",
        )

    # niveau 2
    if n2 and amount_l2 > 0:
        WalletTransaction.objects.create(
            profile=n2,
            type="mlm_commission",
            amount=amount_l2,
            order=order,
            description=f"Commission N2 sur commande {order.code or order.id}",
        )

    # niveau 3
    if n3 and amount_l3 > 0:
        WalletTransaction.objects.create(
            profile=n3,
            type="mlm_commission",
            amount=amount_l3,
            order=order,
            description=f"Commission N3 sur commande {order.code or order.id}",
        )
