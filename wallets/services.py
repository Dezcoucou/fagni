from decimal import Decimal
from typing import Optional

from django.utils import timezone

from orders.models import Order
from .models import Wallet, WalletTransaction, CommissionEvent


def get_or_create_wallet_for_customer(customer) -> Wallet:
    """
    Retourne (ou crée) le wallet associé à un client.
    """
    wallet, _ = Wallet.objects.get_or_create(
        owner_type=Wallet.OWNER_TYPE_CUSTOMER,
        customer=customer,
        defaults={"balance": Decimal("0.00")},
    )
    return wallet


def get_or_create_wallet_for_delivery_partner(delivery_partner) -> Wallet:
    """
    Retourne (ou crée) le wallet associé à un livreur.
    """
    wallet, _ = Wallet.objects.get_or_create(
        owner_type=Wallet.OWNER_TYPE_DELIVERY,
        delivery_partner=delivery_partner,
        defaults={"balance": Decimal("0.00")},
    )
    return wallet


def get_or_create_wallet_for_laundry_partner(laundry_partner) -> Wallet:
    """
    Retourne (ou crée) le wallet associé à une blanchisserie.
    """
    wallet, _ = Wallet.objects.get_or_create(
        owner_type=Wallet.OWNER_TYPE_LAUNDRY,
        laundry_partner=laundry_partner,
        defaults={"balance": Decimal("0.00")},
    )
    return wallet


def credit_wallet(wallet: Wallet, amount: Decimal, label: str, order: Optional[Order] = None, meta: Optional[dict] = None) -> WalletTransaction:
    """
    Crée un crédit sur un wallet (augmentation du solde).

    À utiliser pour :
    - commissions livreur
    - commissions blanchisseur
    - cashback client
    - bonus MLM, etc.
    """
    if amount <= 0:
        raise ValueError("Le montant d'un crédit doit être > 0")

    before = wallet.balance
    after = (before + amount).quantize(Decimal("0.01"))

    wallet.balance = after
    wallet.save(update_fields=["balance", "updated_at"])

    tx = WalletTransaction.objects.create(
        wallet=wallet,
        transaction_type=WalletTransaction.TYPE_CREDIT,
        amount=amount.quantize(Decimal("0.01")),
        balance_before=before,
        balance_after=after,
        label=label,
        order=order,
        meta=meta or {},
    )
    return tx


def debit_wallet(wallet: Wallet, amount: Decimal, label: str, order: Optional[Order] = None, meta: Optional[dict] = None) -> WalletTransaction:
    """
    Crée un débit sur un wallet (diminution du solde),
    par exemple lors d'un retrait ou d'une compensation.
    """
    if amount <= 0:
        raise ValueError("Le montant d'un débit doit être > 0")

    before = wallet.balance
    after = (before - amount).quantize(Decimal("0.01"))

    # À affiner : bloquer si solde négatif, ou autoriser overdraft ?
    if after < 0:
        # Ici on choisit de bloquer par défaut
        raise ValueError("Solde insuffisant sur le wallet")

    wallet.balance = after
    wallet.save(update_fields=["balance", "updated_at"])

    tx = WalletTransaction.objects.create(
        wallet=wallet,
        transaction_type=WalletTransaction.TYPE_DEBIT,
        amount=amount.quantize(Decimal("0.01")),
        balance_before=before,
        balance_after=after,
        label=label,
        order=order,
        meta=meta or {},
    )
    return tx


def create_commission_event_for_order(order: Order) -> CommissionEvent:
    """
    Crée un événement de commission à partir d'une commande.

    Cette fonction sera appelée quand une commande passe à 'done'
    ou quand tu confirmes le paiement complet.

    Pour l'instant on se contente de stocker un snapshot des montants
    clés dans 'payload'. La logique MLM sera ajoutée dans un second temps.
    """
    payload = {
        "order_id": order.id,
        "code": order.code,
        "total": str(order.total or 0),
        "service_fee": str(order.service_fee or 0),
        "delivery_fee": str(order.delivery_fee or 0),
        "logistic_margin": str(order.logistic_margin or 0),
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "status": order.status,
    }

    event = CommissionEvent.objects.create(
        event_type=CommissionEvent.TYPE_ORDER_COMPLETED,
        order=order,
        payload=payload,
        processed=False,
    )
    return event


def mark_commission_event_processed(event: CommissionEvent) -> None:
    """
    Marque un événement comme traité après génération des transactions MLM.
    """
    event.processed = True
    event.processed_at = timezone.now()
    event.save(update_fields=["processed", "processed_at"])
