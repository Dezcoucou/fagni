from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any

from django.db import transaction

from orders.models import Order, Customer
from partners.models import LaundryPartner, DeliveryPartner, RelayPointPartner
from .models import Wallet, WalletTransaction


# ==========================
#  OUTILS DE NORMALISATION
# ==========================
def _to_decimal(amount) -> Decimal:
    """
    Convertit un montant en Decimal avec 2 décimales.
    """
    if isinstance(amount, Decimal):
        value = amount
    else:
        value = Decimal(str(amount))
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ==========================
#  HELPERS GET / CREATE
# ==========================
def get_or_create_wallet_for_customer(customer: Customer) -> Wallet:
    """
    Wallet client FAGNI.
    """
    wallet, _ = Wallet.objects.get_or_create(
        owner_type="customer",
        customer=customer,
        defaults={
            "currency": "XOF",
        },
    )
    return wallet


def get_or_create_wallet_for_delivery_partner(delivery_partner: DeliveryPartner) -> Wallet:
    """
    Wallet livreur partenaire.
    """
    wallet, _ = Wallet.objects.get_or_create(
        owner_type="driver",
        delivery_partner=delivery_partner,
        defaults={
            "currency": "XOF",
        },
    )
    return wallet


def get_or_create_wallet_for_laundry_partner(laundry_partner: LaundryPartner) -> Wallet:
    """
    Wallet blanchisserie partenaire.
    """
    wallet, _ = Wallet.objects.get_or_create(
        owner_type="laundry",
        laundry_partner=laundry_partner,
        defaults={
            "currency": "XOF",
        },
    )
    return wallet


def get_or_create_wallet_for_relay_partner(relay_partner: RelayPointPartner) -> Wallet:
    """
    Wallet point relais partenaire.
    """
    wallet, _ = Wallet.objects.get_or_create(
        owner_type="laundry",  # ou un type dédié si besoin plus tard
        relay_partner=relay_partner,
        defaults={
            "currency": "XOF",
        },
    )
    return wallet


def get_or_create_internal_wallet() -> Wallet:
    """
    Wallet interne FAGNI (compte de la plateforme).
    Utilisé pour encaisser les revenus FAGNI (commissions, service fee, marge, TVA…).
    """
    wallet, _ = Wallet.objects.get_or_create(
        owner_type="internal",
        customer=None,
        laundry_partner=None,
        delivery_partner=None,
        relay_partner=None,
        user=None,
        defaults={
            "currency": "XOF",
        },
    )
    return wallet


# ==========================
#  FONCTIONS GÉNÉRIQUES DE MOUVEMENT
# ==========================
def credit_wallet(
    wallet: Wallet,
    amount,
    label: str = "",
    order: Optional[Order] = None,
    leg=None,
    tx_type: str = "credit",
) -> Optional[WalletTransaction]:
    """
    Crédite un wallet (entrée d'argent).
    - direction = "in"
    - type = tx_type (par ex: "credit", "topup", "payout", "mlm_commission")
    - leg est optionnel (payouts logistiques par tronçon)
    """
    amount = _to_decimal(amount)
    if amount <= 0:
        return None

    wallet.balance = (wallet.balance + amount).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    wallet.save(update_fields=["balance", "updated_at"])

    tx = WalletTransaction.create_tx(
        wallet=wallet,
        order=order,
        leg=leg,
        type=tx_type,
        direction="in",
        amount=amount,
        description=label or "",
        allow_orphan=False,  # interdit si order=None et leg=None
    )
    return tx


def debit_wallet(
    wallet: Wallet,
    amount,
    label: str = "",
    order: Optional[Order] = None,
    leg=None,
    tx_type: str = "debit",
) -> Optional[WalletTransaction]:
    """
    Débite un wallet (sortie d'argent).
    - direction = "out"
    - type = tx_type
    - leg optionnel
    """
    amount = _to_decimal(amount)
    if amount <= 0:
        return None

    wallet.balance = (wallet.balance - amount).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    wallet.save(update_fields=["balance", "updated_at"])

    tx = WalletTransaction.create_tx(
        wallet=wallet,
        order=order,
        leg=leg,
        type=tx_type,
        direction="out",
        amount=amount,
        description=label or "",
        allow_orphan=False,  # interdit si order=None et leg=None
    )
    return tx


# ==========================
#  DISTRIBUTION DES REVENUS D'UNE COMMANDE
# ==========================
@transaction.atomic
def distribute_order_revenues(
    order: Order,
    *,
    recompute: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Distribue les montants d'une commande FAGNI dans les wallets :

    - Vérifie que la commande est PAYÉE (payment_status = "paid")
    - Optionnellement : recalcule les montants via order.update_financials()
    - Crédite :
        • Wallet blanchisseur (amount_laundry_partner)
        • Wallet interne FAGNI (fagni_revenue_ttc)
    - ⚠️ IMPORTANT : le livreur n’est PAS payé ici.
      Le payout livreur se fait UNIQUEMENT à la fin de course (legs done).

    - Marque order.wallets_distributed = True pour éviter les doublons.

    Paramètres :
    - recompute : si True, appelle order.update_financials(save=True)
    - force : si True, redistribue même si wallets_distributed == True

    Retourne un dict avec quelques infos et les ids de transactions créées.
    """
    # 0) Vérifier que la commande est payée
    payment_status = getattr(order, "payment_status", "unpaid")
    if payment_status != "paid":
        raise ValueError(
            f"Impossible de distribuer les wallets : commande {order.id} non payée "
            f"(payment_status = {payment_status})."
        )

    # 1) Éviter la double distribution
    if getattr(order, "wallets_distributed", False) and not force:
        return {
            "status": "already_distributed",
            "order_id": order.id,
        }

    # 2) Recalcul éventuel des montants financiers
    financial_data = None
    if recompute:
        financial_data = order.update_financials(save=True)

    # 3) Récupérer les montants
    prestation_total = _to_decimal(getattr(order, "prestation_total", Decimal("0")))
    service_fee = _to_decimal(getattr(order, "service_fee", Decimal("0")))
    delivery_fee = _to_decimal(getattr(order, "delivery_fee", Decimal("0")))
    amount_laundry = _to_decimal(getattr(order, "amount_laundry_partner", Decimal("0")))
    amount_driver = _to_decimal(getattr(order, "amount_driver_partner", Decimal("0")))
    fagni_ht = _to_decimal(getattr(order, "fagni_revenue_ht", Decimal("0")))
    fagni_ttc = _to_decimal(getattr(order, "fagni_revenue_ttc", Decimal("0")))

    txs: Dict[str, Optional[WalletTransaction]] = {}

    # 4.a Wallet blanchisseur
    if order.laundry_partner and amount_laundry > 0:
        wl = get_or_create_wallet_for_laundry_partner(order.laundry_partner)
        txs["laundry"] = credit_wallet(
            wl,
            amount_laundry,
            label=f"Commande {order.code} – part blanchisserie",
            order=order,
            tx_type="payout",
        )
    else:
        txs["laundry"] = None

    # 4.b Wallet livreur
    # ⚠️ NE PAS payer au paiement — payé à la fin ("done") via legs.
    txs["driver"] = None

    # 4.c Wallet interne FAGNI (revenu plateforme)
    internal_wallet = get_or_create_internal_wallet()
    if fagni_ttc > 0:
        txs["internal"] = credit_wallet(
            internal_wallet,
            fagni_ttc,
            label=f"Commande {order.code} – revenu FAGNI TTC",
            order=order,
            tx_type="credit",
        )
    else:
        txs["internal"] = None

    # 5) Marquer comme distribuée UNIQUEMENT si on a créé au moins 1 transaction
    created_any = any([
        txs.get("laundry") is not None,
        txs.get("internal") is not None,
    ])
    if created_any:
        order.wallets_distributed = True
        order.save(update_fields=["wallets_distributed"])

    # 6) Retour d'info
    return {
        "status": "ok",
        "order_id": order.id,
        "payment_status": payment_status,
        "amounts": {
            "prestation_total": prestation_total,
            "service_fee": service_fee,
            "delivery_fee": delivery_fee,
            "amount_laundry": amount_laundry,
            "amount_driver": amount_driver,
            "fagni_revenue_ht": fagni_ht,
            "fagni_revenue_ttc": fagni_ttc,
        },
        "transactions": {
            key: tx.id if tx else None
            for key, tx in txs.items()
        },
        "financial_data": financial_data,
    }
