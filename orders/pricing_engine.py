"""
FAGNI Pricing Engine v2.0
Modèle : prix à l'article — 200 FCFA pressing, prix fixe client par sac
"""
from decimal import Decimal, ROUND_HALF_UP


def d(val):
    return Decimal(str(val or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


PRIX_ARTICLE_PRESSING  = 200
TAUX_LIVREUR           = Decimal('0.70')
TAUX_FAGNI_LIVRAISON   = Decimal('0.30')
SERVICE_FEE_MIN        = 500

BAG_CONFIG = {
    'small':  {'label': 'Petit sac',  'articles': 15, 'prix_client': 9000,  'delivery': 2000, 'poids_kg': 5},
    'medium': {'label': 'Sac moyen',  'articles': 25, 'prix_client': 13500, 'delivery': 2000, 'poids_kg': 10},
    'large':  {'label': 'Grand sac',  'articles': 50, 'prix_client': 25000, 'delivery': 2000, 'poids_kg': 15},
}


def calculate_order(bag_size='small', nb_articles=None, delivery_fee=None):
    config        = BAG_CONFIG.get(bag_size, BAG_CONFIG['small'])
    articles      = nb_articles if nb_articles else config['articles']
    total_client  = d(config['prix_client'])
    livraison     = d(delivery_fee if delivery_fee else config['delivery'])

    part_livreur    = (livraison * TAUX_LIVREUR).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    marge_livraison = livraison - part_livreur
    part_pressing   = d(articles * PRIX_ARTICLE_PRESSING)

    sous_total  = (total_client / Decimal('1.05')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    service_fee = total_client - sous_total
    if service_fee < d(SERVICE_FEE_MIN):
        service_fee = d(SERVICE_FEE_MIN)

    marge_pressing = total_client - part_pressing - livraison - service_fee
    total_fagni    = marge_pressing + marge_livraison + service_fee

    return {
        'total_client':      int(total_client),
        'delivery_fee':      int(livraison),
        'service_fee':       int(service_fee),
        'part_pressing':     int(part_pressing),
        'part_livreur':      int(part_livreur),
        'marge_pressing':    int(marge_pressing),
        'marge_livraison':   int(marge_livraison),
        'total_fagni':       int(total_fagni),
        'fagni_revenue_ht':  int(total_fagni),
        'bag_size':          bag_size,
        'bag_label':         config['label'],
        'nb_articles':       articles,
        'prix_article_client': int((total_client - livraison - service_fee) / articles),
        # Compatibilité ancien code
        'total_client_ttc':  int(total_client),
        'amount_laundry_partner': int(part_pressing),
        'amount_driver_partner':  int(part_livreur),
        'commission_pressing':    int(marge_pressing),
        'service_fee_amount':     int(service_fee),
    }


def get_bag_pricing():
    result = {}
    for size, config in BAG_CONFIG.items():
        p = calculate_order(size)
        result[size] = {
            'label':       config['label'],
            'articles':    config['articles'],
            'poids_kg':    config['poids_kg'],
            'prix_client': config['prix_client'],
            'delivery_fee': config['delivery'],
            'service_fee': p['service_fee'],
            'part_pressing': p['part_pressing'],
            'part_livreur':  p['part_livreur'],
            'total_fagni':   p['total_fagni'],
        }
    return result


def format_receipt(pricing):
    return (
        f"🧾 Récapitulatif FAGNI\n"
        f"━━━━━━━━━━━━━━\n"
        f"🧺 {pricing['bag_label']} ({pricing['nb_articles']} articles)\n"
        f"🚗 Livraison AR  : {pricing['delivery_fee']:,} FCFA\n"
        f"⚡ Service fee   : {pricing['service_fee']:,} FCFA\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 TOTAL         : {pricing['total_client']:,} FCFA\n"
    )


calculate_bag_pricing  = lambda bag_size, zone='standard': calculate_order(bag_size)
compute_order_pricing  = calculate_order
