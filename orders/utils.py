def build_order_display_context(order, customer=None, amounts=None, paid=0):
    total = (amounts or {}).get("total_ttc", 0)

    return {
        "total_amount": total,
        "paid_amount": paid,
        "remain_amount": max(total - paid, 0),
        "display_address": (
            getattr(order, "pickup_address", None)
            or getattr(customer, "address", None) if customer else None
            or "Adresse non renseignée"
        ),
    }
