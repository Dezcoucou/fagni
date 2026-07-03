from __future__ import annotations

SERVICE_TYPE_CHOICES = [
    ("pressing", "Pressing"),
    ("cordonnerie", "Cordonnerie"),
    ("chaussures", "Chaussures"),
    ("retouche", "Retouche"),
]

SERVICE_TYPE_CHOICES_WITH_BLANK = [
    ("", "À déduire"),
    *SERVICE_TYPE_CHOICES,
]

ORDER_STATUS_CANONICAL = [
    ("draft", "Brouillon"),
    ("submitted", "Soumise"),
    ("confirmed", "Confirmée"),
    ("assigned", "Affectée"),
    ("picked_up", "Collectée"),
    ("in_processing", "En traitement"),
    ("ready_for_delivery", "Prête à livrer"),
    ("delivering", "En livraison"),
    ("done", "Terminée"),
    ("canceled", "Annulée"),
]

PAYMENT_STATUS_CANONICAL = [
    ("unpaid", "Impayé"),
    ("partial", "Partiel"),
    ("paid", "Payé"),
    ("refunded", "Remboursé"),
    ("failed", "Échoué"),
]

LEG_TYPE_CANONICAL = [
    ("pickup_client", "Collecte client"),
    ("drop_partner", "Dépôt partenaire"),
    ("pickup_partner", "Récupération partenaire"),
    ("drop_client", "Livraison client"),
]


def infer_service_type_from_order_item(item) -> str:
    """
    Déduction douce et non destructive du type de service à partir de:
    - item.service
    - item.service.category
    - item.designation

    Priorités FAGNI V1:
    1. retouche
    2. cordonnerie
    3. chaussures
    4. pressing
    """
    chunks = []

    try:
        if getattr(item, "service", None):
            svc = item.service
            chunks.append(str(getattr(svc, "name", "") or ""))
            chunks.append(str(getattr(svc, "label", "") or ""))
            chunks.append(str(getattr(svc, "designation", "") or ""))

            category = getattr(svc, "category", None)
            if category:
                chunks.append(str(getattr(category, "name", "") or ""))
                chunks.append(str(getattr(category, "label", "") or ""))
    except Exception:
        import logging
        logging.getLogger("fagni.orders.domain_canonical").exception("Exception silencieuse (auto-log) - fichier=orders/domain_canonical.py ligne=70")

    try:
        chunks.append(str(getattr(item, "designation", "") or ""))
    except Exception:
        import logging
        logging.getLogger("fagni.orders.domain_canonical").exception("Exception silencieuse (auto-log) - fichier=orders/domain_canonical.py ligne=75")

    text = " ".join(chunks).strip().lower()

    if not text:
        return "pressing"

    # 1) Retouche
    if any(k in text for k in [
        "retouche", "ourlet", "couture", "ajustement", "reprise",
        "retrecir", "rétrécir", "elargir", "élargir"
    ]):
        return "retouche"

    # 2) Cordonnerie = réparation / structure
    if any(k in text for k in [
        "cordonnerie", "cordonnier", "semelle", "talon", "cuir",
        "réparation", "reparation", "collage", "ressemelage"
    ]):
        return "cordonnerie"

    # 3) Chaussures = articles chaussants
    if any(k in text for k in [
        "chaussure", "chaussures", "basket", "baskets", "sneaker", "sneakers",
        "soulier", "souliers", "mocassin", "mocassins", "sandale", "sandales",
        "escarpin", "escarpins", "babouche", "babouches", "botte", "bottes"
    ]):
        return "chaussures"

    # 4) Pressing = vêtements / linge / textile
    if any(k in text for k in [
        "chemise", "chemises", "pantalon", "pantalons", "robe", "robes",
        "jupe", "jupes", "tee-shirt", "t-shirt", "shirt", "tunique",
        "pull", "pull-over", "veste", "vestes", "costume", "costumes",
        "blouson", "short", "shorts", "jean", "jeans", "boubou", "pagne",
        "drap", "draps", "couette", "linge", "textile", "habit", "habits"
    ]):
        return "pressing"

    return "pressing"


def map_order_status_to_canonical(status: str) -> str:
    s = (status or "").strip().lower()
    mapping = {
        "draft": "draft",
        "pending": "submitted",
        "submitted": "submitted",
        "confirmed": "confirmed",
        "assigned": "assigned",
        "picked_up": "picked_up",
        "in_progress": "in_processing",
        "processing": "in_processing",
        "ready_for_delivery": "ready_for_delivery",
        "delivering": "delivering",
        "done": "done",
        "completed": "done",
        "canceled": "canceled",
        "cancelled": "canceled",
    }
    return mapping.get(s, s or "submitted")


def map_payment_status_to_canonical(status: str) -> str:
    s = (status or "").strip().lower()
    mapping = {
        "unpaid": "unpaid",
        "partial": "partial",
        "paid": "paid",
        "refunded": "refunded",
        "failed": "failed",
    }
    return mapping.get(s, "unpaid")


def map_leg_type_to_canonical(leg_type: str) -> str:
    s = (leg_type or "").strip().lower()
    mapping = {
        "pickup": "pickup_client",
        "pickup_client": "pickup_client",
        "drop_partner": "drop_partner",
        "pickup_partner": "pickup_partner",
        "return": "drop_client",
        "drop_client": "drop_client",
    }
    return mapping.get(s, s or "pickup_client")
