from __future__ import annotations


def canonical_leg_status(leg) -> str:
    raw = (getattr(leg, "status", None) or "").strip().lower()
    driver_id = getattr(leg, "driver_id", None)

    if raw in ("canceled", "cancelled"):
        return "canceled"
    if raw == "done":
        return "done"
    if raw == "in_progress":
        return "in_progress"
    if raw == "assigned":
        return "assigned"

    if raw == "pending" and driver_id:
        return "assigned"

    return "pending"


def normalize_order_legs(order, save: bool = True) -> dict:
    """
    Normalisation légère et non destructive des DeliveryLeg d'une commande.
    - Ne rétrograde jamais done/canceled
    - Si driver affecté sur pending -> assigned
    - Si pickup done et return a un driver -> return peut devenir assigned
    """
    try:
        legs = list(order.legs.all().order_by("id"))
    except Exception:
        legs = []

    result = {
        "count": len(legs),
        "updated": 0,
        "details": [],
    }

    if not legs:
        return result

    pickup_leg = None
    return_leg = None

    for leg in legs:
        leg_type = (getattr(leg, "leg_type", None) or "").strip().lower()
        if leg_type == "pickup" and pickup_leg is None:
            pickup_leg = leg
        elif leg_type == "return" and return_leg is None:
            return_leg = leg

    for leg in legs:
        old = (getattr(leg, "status", None) or "").strip().lower()
        new = canonical_leg_status(leg)

        # règle supplémentaire sur return
        if leg is return_leg:
            if pickup_leg is not None:
                pickup_status = (getattr(pickup_leg, "status", None) or "").strip().lower()
                if pickup_status not in ("done",):
                    # return ne doit pas avancer trop tôt
                    if new in ("assigned", "in_progress"):
                        if getattr(leg, "driver_id", None):
                            new = "assigned"
                        else:
                            new = "pending"

        if old != new:
            leg.status = new
            if save:
                leg.save(update_fields=["status"])
            result["updated"] += 1
            result["details"].append({
                "leg_id": getattr(leg, "id", None),
                "leg_type": getattr(leg, "leg_type", None),
                "old": old,
                "new": new,
            })

    return result
