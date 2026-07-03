from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from orders.domain_canonical import (
    map_order_status_to_canonical,
    map_payment_status_to_canonical,
)


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal("0")


def _q2(v) -> Decimal:
    return _dec(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _build_display_address(order) -> str:
    candidates = []

    try:
        candidates.append(getattr(order, "pickup_address", None))
    except Exception:
        import logging
        logging.getLogger("fagni.orders.utils.canonical_snapshot").exception("Exception silencieuse (auto-log) - fichier=orders/utils/canonical_snapshot.py ligne=27")

    try:
        candidates.append(getattr(order, "delivery_address", None))
    except Exception:
        import logging
        logging.getLogger("fagni.orders.utils.canonical_snapshot").exception("Exception silencieuse (auto-log) - fichier=orders/utils/canonical_snapshot.py ligne=32")

    try:
        customer = getattr(order, "customer", None)
        if customer:
            candidates.append(getattr(customer, "address", None))
    except Exception:
        import logging
        logging.getLogger("fagni.orders.utils.canonical_snapshot").exception("Exception silencieuse (auto-log) - fichier=orders/utils/canonical_snapshot.py ligne=39")

    for raw in candidates:
        try:
            val = str(raw or "").strip()
        except Exception:
            val = ""
        if not val:
            continue
        # rejette valeurs trop courtes / probablement cassées
        if len(val) < 5:
            continue
        return val

    return "Adresse non renseignée"


def _status_label(status_canonical: str) -> str:
    mapping = {
        "draft": "Brouillon",
        "submitted": "Soumise",
        "confirmed": "Confirmée",
        "assigned": "Affectée",
        "picked_up": "Collectée",
        "in_processing": "En traitement",
        "ready_for_delivery": "Prête à livrer",
        "delivering": "En livraison",
        "done": "Terminée",
        "canceled": "Annulée",
    }
    return mapping.get(status_canonical or "", status_canonical or "—")


def _payment_label(payment_status_canonical: str) -> str:
    mapping = {
        "unpaid": "En attente",
        "partial": "Partiel",
        "paid": "Payé",
        "refunded": "Remboursé",
        "failed": "Échoué",
    }
    return mapping.get(payment_status_canonical or "", payment_status_canonical or "—")


def canonical_order_status(order) -> str:
    raw = (getattr(order, "status", None) or "").strip().lower()

    if raw in ("canceled", "cancelled"):
        return "canceled"
    if raw in ("done", "completed"):
        return "done"

    try:
        legs = list(order.legs.all())
    except Exception:
        legs = []

    active_legs = [leg for leg in legs if (getattr(leg, "status", None) or "").strip().lower() != "canceled"]
    done_legs = [leg for leg in active_legs if (getattr(leg, "status", None) or "").strip().lower() == "done"]
    in_progress_legs = [leg for leg in active_legs if (getattr(leg, "status", None) or "").strip().lower() == "in_progress"]
    assigned_legs = [leg for leg in active_legs if (getattr(leg, "status", None) or "").strip().lower() == "assigned"]
    pending_legs = [leg for leg in active_legs if (getattr(leg, "status", None) or "").strip().lower() == "pending"]

    # si une jambe finale est en cours de livraison
    for leg in active_legs:
        leg_type = (getattr(leg, "leg_type", None) or "").strip().lower()
        leg_status = (getattr(leg, "status", None) or "").strip().lower()
        if leg_type in ("drop_client", "return") and leg_status == "in_progress":
            return "delivering"

    # priorité au statut brut de la commande si elle est déjà en cours
    if raw in ("in_progress", "processing"):
        return "in_processing"

    # sinon, lecture par les jambes
    if in_progress_legs:
        return "in_processing"

    if assigned_legs:
        return "assigned"

    if done_legs and len(done_legs) < len(active_legs):
        return "ready_for_delivery"

    if active_legs and pending_legs:
        return "submitted"

    return map_order_status_to_canonical(raw)


def canonical_payment_status(order) -> str:
    raw = getattr(order, "payment_status", None)
    return map_payment_status_to_canonical(raw)


def build_order_canonical_snapshot(order) -> dict:
    customer = getattr(order, "customer", None)

    total_client_ttc = _q2(getattr(order, "total_client_ttc", 0))
    if total_client_ttc <= 0:
        total_client_ttc = _q2(getattr(order, "total", 0))

    amount_paid = _q2(getattr(order, "amount_paid", 0))
    amount_due = total_client_ttc - amount_paid
    if amount_due < 0:
        amount_due = Decimal("0.00")

    prestation_total = _q2(getattr(order, "prestation_total", 0))
    delivery_fee = _q2(getattr(order, "delivery_fee", 0))
    amount_driver_partner = _q2(getattr(order, "amount_driver_partner", 0))
    logistic_margin = _q2(getattr(order, "logistic_margin", 0))
    fagni_revenue_ht = _q2(getattr(order, "fagni_revenue_ht", 0))
    vat_fagni = _q2(getattr(order, "vat_fagni", 0))
    fagni_revenue_ttc = _q2(getattr(order, "fagni_revenue_ttc", 0))
    amount_laundry_partner = _q2(getattr(order, "amount_laundry_partner", 0))

    status_raw = getattr(order, "status", None)
    status_canonical = canonical_order_status(order)

    payment_status_raw = getattr(order, "payment_status", None)
    payment_status_canonical = canonical_payment_status(order)

    try:
        legs_qs = order.legs.all()
        legs = list(legs_qs)
    except Exception:
        legs = []

    legs_count = len(legs)
    active_legs_count = sum(1 for leg in legs if getattr(leg, "status", None) != "canceled")
    done_legs_count = sum(1 for leg in legs if getattr(leg, "status", None) == "done")

    display_address = _build_display_address(order)

    code = getattr(order, "code", None) or getattr(order, "id", None)

    customer_name = ""
    customer_phone = ""
    if customer is not None:
        customer_name = str(getattr(customer, "name", "") or "")
        customer_phone = str(getattr(customer, "phone", "") or "")

    snapshot = {
        "id": getattr(order, "id", None),
        "code": code,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "display_address": display_address,

        "status_raw": status_raw,
        "status_canonical": status_canonical,
        "status_label": _status_label(status_canonical),

        "payment_status_raw": payment_status_raw,
        "payment_status_canonical": payment_status_canonical,
        "payment_label": _payment_label(payment_status_canonical),

        "prestation_total": prestation_total,
        "delivery_fee": delivery_fee,
        "amount_driver_partner": amount_driver_partner,
        "amount_laundry_partner": amount_laundry_partner,
        "logistic_margin": logistic_margin,
        "fagni_revenue_ht": fagni_revenue_ht,
        "vat_fagni": vat_fagni,
        "fagni_revenue_ttc": fagni_revenue_ttc,

        "total_client_ttc": total_client_ttc,
        "amount_paid": amount_paid,
        "amount_due": amount_due,

        "distance_km": _q2(getattr(order, "distance_km", 0)),
        "legs_count": legs_count,
        "active_legs_count": active_legs_count,
        "done_legs_count": done_legs_count,

        "is_paid": payment_status_canonical == "paid",
        "is_done": status_canonical == "done",
        "can_pay": (payment_status_canonical != "paid" and total_client_ttc > 0),
    }

    return snapshot
