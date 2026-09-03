"""
Helpers wallet pour les cotransporteurs Crowd.
"""
from django.db import transaction
from wallets.models import Wallet


def get_or_create_wallet_for_cotransporter(cotransporter):
    """
    Récupère ou crée le wallet d'un cotransporteur.
    
    Utilise le même mécanisme que les DeliveryPartner :
    - Wallet.owner_type = 'cotransporter'
    - Wallet.cotransporter = cotransporter
    """
    from crowd.models import Cotransporter
    
    if not cotransporter:
        return None
    
    with transaction.atomic():
        wallet, created = Wallet.objects.get_or_create(
            owner_type='cotransporter',
            cotransporter_id=cotransporter.id,
            defaults={
                'currency': 'XOF',
                'balance': 0,
            }
        )
        return wallet
