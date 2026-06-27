"""
FAGNI Pricing Engine v4.0
Moteur de pricing parametrique -- tous les montants depuis GlobalPricingSettings.
Aucune valeur hardcodee dans ce fichier.
"""
from decimal import Decimal, ROUND_HALF_UP


def d(val):
    return Decimal(str(val or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def _get_cfg():
    """Retourne GlobalPricingSettings singleton -- source de verite unique."""
    from orders.config_models import GlobalPricingSettings
    return GlobalPricingSettings.get_solo()


ECART_ABSORBE = 3  # Ecart articles absorbe par FAGNI (non parametre)

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
    Calcul complet d'une commande FAGNI v4.0
    Tous les parametres lus depuis GlobalPricingSettings.
    Args:
        nb_articles  : nombre d'articles declares par le client
        bag_size     : 'small', 'medium', 'large' (repere UX)
        delivery_fee : frais livraison AR reels (calcules au km ou fallback delivery_min_fee)
    """
    cfg = _get_cfg()

    # Lecture parametres depuis GlobalPricingSettings
    prix_article_client   = int(cfg.prix_article_fcfa or 500)
    commission_pct        = Decimal(str(cfg.laundry_commission_percent or 20)) / 100
    prix_article_pressing = int(round(prix_article_client * (1 - float(commission_pct))))
    marge_article_fagni   = prix_article_client - prix_article_pressing
    remun_leg             = int(cfg.driver_amount_per_leg or 800)
    service_fee_rate      = Decimal(str(cfg.service_fee_percent or 5)) / 100
    service_fee_min       = int(cfg.service_fee_min or 500)
    delivery_min_fee      = int(cfg.delivery_min_fee or 2000)

    articles  = max(1, int(nb_articles))
    livraison = d(delivery_fee if delivery_fee is not None else delivery_min_fee)

    # Pressing
    part_pressing  = d(articles * prix_article_pressing)
    marge_pressing = d(articles * marge_article_fagni)

    # Sous-total avant service fee
    sous_total_pressing = d(articles * prix_article_client)
    sous_total = sous_total_pressing + livraison

    # Service fee calcule sur le sous-total pressing uniquement
    service_fee = (sous_total_pressing * service_fee_rate).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP)
    if service_fee < d(service_fee_min):
        service_fee = d(service_fee_min)

    # Total client
    total_client = sous_total + service_fee

    # Livraison — 2 jambes (collecte + retour)
    part_livreur    = d(remun_leg * 2)
    marge_livraison = livraison - part_livreur

    # Total FAGNI
    total_fagni = marge_pressing + marge_livraison + service_fee

    config = BAG_CONFIG.get(bag_size, BAG_CONFIG['small'])
    return {
        'total_client':          int(total_client),
        'total_client_ttc':      int(total_client),
        'delivery_fee':          int(livraison),
        'service_fee':           int(service_fee),
        'pressing_client':       int(sous_total_pressing),
        'part_pressing':         int(part_pressing),
        'amount_laundry_partner':int(part_pressing),
        'part_livreur':          int(part_livreur),
        'amount_driver_partner': int(part_livreur),
        'remuneration_collecte': remun_leg,
        'remuneration_livraison':remun_leg,
        'marge_pressing':        int(marge_pressing),
        'marge_livraison':       int(marge_livraison),
        'total_fagni':           int(total_fagni),
        'fagni_revenue_ht':      int(total_fagni),
        'bag_size':              bag_size,
        'bag_label':             config['label'],
        'nb_articles':           articles,
        'prix_article_client':   prix_article_client,
        'prix_article_pressing': prix_article_pressing,
        'marge_article_fagni':   marge_article_fagni,
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
