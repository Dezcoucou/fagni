"""
FAGNI Pricing Engine v3.0
Modèle hybride premium :
- Sac = repère visuel UX uniquement
- Facturation réelle à l'article : 500 FCFA/article client
- Pressing reçoit : 200 FCFA/article
- FAGNI marge pressing : 300 FCFA/article
- Livraison AR : 2 000 FCFA (livreur 70%, FAGNI 30%)
- Service fee : 5% du sous-total, min 500 FCFA
- Écart ≤ 3 articles à la collecte → FAGNI absorbe
"""
from decimal import Decimal, ROUND_HALF_UP


def d(val):
    return Decimal(str(val or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


# Taux officiels FAGNI v3.0
PRIX_ARTICLE_CLIENT   = 500   # FCFA — ce que le client paie par article
PRIX_ARTICLE_PRESSING = 200   # FCFA — ce que le pressing reçoit
MARGE_FAGNI_ARTICLE   = 300   # FCFA — marge FAGNI par article
TAUX_LIVREUR          = Decimal('0.70')  # 70% livraison au livreur
TAUX_FAGNI_LIVRAISON  = Decimal('0.30')  # 30% livraison à FAGNI
SERVICE_FEE_RATE      = Decimal('0.05')  # 5% du sous-total
SERVICE_FEE_MIN       = 500             # Minimum 500 FCFA
DELIVERY_FEE          = 2000            # Livraison AR fixe
ECART_ABSORBE         = 3              # Écart articles absorbé par FAGNI

# Configuration des sacs — repères UX uniquement
BAG_CONFIG = {
    'small': {
        'label':      'Petit sac',
        'max_items':  15,
        'poids_kg':   5,
        'emoji':      '🧳',
        'description': 'Idéal pour 1 personne — chemises, t-shirts, pantalons',
    },
    'medium': {
        'label':      'Sac moyen',
        'max_items':  25,
        'poids_kg':   10,
        'emoji':      '🎒',
        'description': 'Idéal pour un couple — usage régulier',
    },
    'large': {
        'label':      'Grand sac',
        'max_items':  50,
        'poids_kg':   15,
        'emoji':      '👜',
        'description': 'Idéal pour une famille — grand volume',
    },
}


def calculate_order(nb_articles, bag_size='small', delivery_fee=None):
    """
    Calcul complet d'une commande FAGNI v3.0

    Args:
        nb_articles  : nombre d'articles déclarés par le client
        bag_size     : 'small', 'medium', 'large' (repère UX)
        delivery_fee : frais livraison AR (défaut 2000 FCFA)

    Returns:
        dict avec tous les montants décomposés
    """
    articles     = max(1, int(nb_articles))
    livraison    = d(delivery_fee or DELIVERY_FEE)

    # Pressing
    part_pressing = d(articles * PRIX_ARTICLE_PRESSING)
    marge_pressing = d(articles * MARGE_FAGNI_ARTICLE)

    # Sous-total avant service fee
    sous_total_pressing = d(articles * PRIX_ARTICLE_CLIENT)
    sous_total = sous_total_pressing + livraison

    # Service fee
    service_fee = (sous_total * SERVICE_FEE_RATE).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP)
    if service_fee < d(SERVICE_FEE_MIN):
        service_fee = d(SERVICE_FEE_MIN)

    # Total client
    total_client = sous_total + service_fee

    # Livraison
    part_livreur    = (livraison * TAUX_LIVREUR).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP)
    marge_livraison = livraison - part_livreur

    # Total FAGNI
    total_fagni = marge_pressing + marge_livraison + service_fee

    # Vérification
    check = part_pressing + livraison + service_fee + marge_pressing
    assert check == total_client, f"Erreur calcul: {check} ≠ {total_client}"

    config = BAG_CONFIG.get(bag_size, BAG_CONFIG['small'])

    return {
        # Ce que le client paie
        'total_client':          int(total_client),
        'total_client_ttc':      int(total_client),
        'delivery_fee':          int(livraison),
        'service_fee':           int(service_fee),
        'pressing_client':       int(sous_total_pressing),

        # Ce que FAGNI redistribue
        'part_pressing':         int(part_pressing),
        'amount_laundry_partner': int(part_pressing),
        'part_livreur':          int(part_livreur),
        'amount_driver_partner': int(part_livreur),

        # Revenus FAGNI
        'marge_pressing':        int(marge_pressing),
        'marge_livraison':       int(marge_livraison),
        'total_fagni':           int(total_fagni),
        'fagni_revenue_ht':      int(total_fagni),

        # Infos commande
        'bag_size':              bag_size,
        'bag_label':             config['label'],
        'nb_articles':           articles,
        'prix_article_client':   PRIX_ARTICLE_CLIENT,
        'prix_article_pressing': PRIX_ARTICLE_PRESSING,
        'marge_article_fagni':   MARGE_FAGNI_ARTICLE,
        'ecart_absorbe':         ECART_ABSORBE,
    }


def get_bag_pricing():
    """Retourner les infos des sacs pour l'app client."""
    result = {}
    for size, config in BAG_CONFIG.items():
        # Exemple avec articles max
        p = calculate_order(config['max_items'], size)
        result[size] = {
            'label':               config['label'],
            'max_items':           config['max_items'],
            'poids_kg':            config['poids_kg'],
            'emoji':               config['emoji'],
            'description':         config['description'],
            'prix_article_client': PRIX_ARTICLE_CLIENT,
            'prix_article_pressing': PRIX_ARTICLE_PRESSING,
            'delivery_fee':        DELIVERY_FEE,
            'service_fee_rate':    float(SERVICE_FEE_RATE),
            'service_fee_min':     SERVICE_FEE_MIN,
            'exemple_max': {
                'nb_articles':  config['max_items'],
                'total_client': p['total_client'],
                'service_fee':  p['service_fee'],
            }
        }
    return result


def format_receipt(pricing):
    """Récapitulatif WhatsApp."""
    return (
        f"🧾 Récapitulatif FAGNI\n"
        f"━━━━━━━━━━━━━━\n"
        f"🧺 {pricing['bag_label']} — {pricing['nb_articles']} articles\n"
        f"   {pricing['nb_articles']} × 500 FCFA\n"
        f"🚗 Livraison AR  : {pricing['delivery_fee']:,} FCFA\n"
        f"⚡ Service fee   : {pricing['service_fee']:,} FCFA\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 TOTAL         : {pricing['total_client']:,} FCFA\n"
        f"📌 Total confirmé à la collecte\n"
    )


# Test rapide
if __name__ == '__main__':
    print("=== TEST PRICING FAGNI v3.0 ===\n")
    for articles, bag in [(12,'small'),(15,'small'),(25,'medium'),(40,'large'),(50,'large')]:
        p = calculate_order(articles, bag)
        print(f"{p['bag_label']} — {articles} articles")
        print(f"  Pressing   : {p['part_pressing']:,} FCFA ({articles} × 200)")
        print(f"  Livraison  : {p['delivery_fee']:,} FCFA")
        print(f"  Service fee: {p['service_fee']:,} FCFA")
        print(f"  TOTAL      : {p['total_client']:,} FCFA")
        print(f"  FAGNI      : {p['total_fagni']:,} FCFA")
        print()

calculate_bag_pricing = lambda bag_size, zone='standard': calculate_order(
    BAG_CONFIG.get(bag_size, BAG_CONFIG['small'])['max_items'], bag_size)
compute_order_pricing = calculate_order
