"""
FAGNI — Moteur de pricing centralisé
Toute l'application utilise calculate_order_total()
"""

PRICE_PER_ARTICLE = 500
DELIVERY_FEE      = 2000
SERVICE_FEE_RATE  = 0.05
SERVICE_FEE_MIN   = 500


def calculate_order_total(articles_count: int) -> dict:
    """
    Calcule le total officiel FAGNI.
    
    Args:
        articles_count: nombre d'articles réels collectés
    
    Returns:
        {
            "articles_count": int,
            "articles_total": int,
            "delivery_fee": int,
            "service_fee": int,
            "total": int
        }
    """
    articles_count  = max(0, int(articles_count))
    articles_total  = articles_count * PRICE_PER_ARTICLE
    service_fee     = max(SERVICE_FEE_MIN, int(articles_total * SERVICE_FEE_RATE))
    total           = articles_total + DELIVERY_FEE + service_fee

    return {
        "articles_count":  articles_count,
        "articles_total":  articles_total,
        "delivery_fee":    DELIVERY_FEE,
        "service_fee":     service_fee,
        "total":           total,
    }


# Tests rapides
