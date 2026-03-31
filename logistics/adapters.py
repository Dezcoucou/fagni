from core.models import Address
from partners.models import LaundryPartner


def _clean_text(value):
    return (str(value).strip() if value is not None else "")


def _looks_like_address_instance(value):
    return isinstance(value, Address)


def get_or_create_v2_address_from_text(
    *,
    raw_text,
    label="",
    contact_name="",
    contact_phone="",
    city="",
    commune="",
    district="",
    street="",
    landmark="",
    instructions="",
):
    raw_text = _clean_text(raw_text)
    if not raw_text:
        return None

    obj, _created = Address.objects.get_or_create(
        full_address=raw_text,
        defaults={
            "label": _clean_text(label) or raw_text[:80],
            "contact_name": _clean_text(contact_name),
            "phone": _clean_text(contact_phone),
            "city": _clean_text(city),
            "commune": _clean_text(commune),
            "district": _clean_text(district),
            "street": _clean_text(street),
            "landmark": _clean_text(landmark),
            "instructions": _clean_text(instructions),
            "is_default": False,
        },
    )
    return obj


def get_order_source_address_v2(order):
    """
    Retourne un objet core.Address à partir d'une commande legacy ou V2-compatible.
    Priorité :
    1. order.pickup_address si c'est déjà un Address
    2. order.pickup_address si c'est du texte
    3. order.address / full_address / pickup_address_text si disponibles
    4. fallback première Address existante
    """
    candidates = [
        getattr(order, "pickup_address", None),
        getattr(order, "address", None),
        getattr(order, "full_address", None),
        getattr(order, "pickup_address_text", None),
    ]

    for candidate in candidates:
        if _looks_like_address_instance(candidate):
            return candidate

    raw_text = ""
    for candidate in candidates:
        if candidate and not _looks_like_address_instance(candidate):
            raw_text = _clean_text(candidate)
            if raw_text:
                break

    if raw_text:
        return get_or_create_v2_address_from_text(
            raw_text=raw_text,
            label=f"Order {getattr(order, 'id', '')} pickup",
        )

    return Address.objects.order_by("id").first()


def get_order_destination_address_v2(order):
    """
    Retourne un objet core.Address à partir d'une commande legacy ou V2-compatible.
    Priorité :
    1. order.delivery_address si c'est déjà un Address
    2. order.delivery_address si c'est du texte
    3. fallback sur pickup/source
    4. fallback dernière Address existante
    """
    candidates = [
        getattr(order, "delivery_address", None),
        getattr(order, "return_address", None),
        getattr(order, "delivery_address_text", None),
    ]

    for candidate in candidates:
        if _looks_like_address_instance(candidate):
            return candidate

    raw_text = ""
    for candidate in candidates:
        if candidate and not _looks_like_address_instance(candidate):
            raw_text = _clean_text(candidate)
            if raw_text:
                break

    if raw_text:
        return get_or_create_v2_address_from_text(
            raw_text=raw_text,
            label=f"Order {getattr(order, 'id', '')} delivery",
        )

    return get_order_source_address_v2(order) or Address.objects.order_by("-id").first()


def get_order_contact_name(order):
    """
    Essaie de récupérer un nom de contact utile depuis la commande legacy.
    """
    candidates = [
        getattr(order, "customer_name", None),
        getattr(order, "full_name", None),
        getattr(order, "name", None),
    ]
    for candidate in candidates:
        value = _clean_text(candidate)
        if value:
            return value
    return "Client FAGNI"


def get_order_contact_phone(order):
    """
    Essaie de récupérer un téléphone utile depuis la commande legacy.
    """
    candidates = [
        getattr(order, "customer_phone", None),
        getattr(order, "phone", None),
        getattr(order, "contact_phone", None),
    ]

    customer = getattr(order, "customer", None)
    if customer is not None:
        candidates.append(getattr(customer, "phone", None))

    for candidate in candidates:
        value = _clean_text(candidate)
        if value:
            return value

    return ""


def get_or_create_smoke_partner():
    """
    Retourne un LaundryPartner utilisable pour les tests / flux V2.
    """
    partner = LaundryPartner.objects.order_by("id").first()
    if partner:
        return partner

    return LaundryPartner.objects.create(
        name="Blanchisserie Smoke Test",
        phone="0700001111",
        email="smoke.partner@fagni.test",
        address="Abidjan - Cocody",
        city="Abidjan",
        notes="Partenaire créé automatiquement par adapter V2",
        is_active=True,
    )
