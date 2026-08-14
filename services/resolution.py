from __future__ import annotations

from orders.domain_canonical import infer_service_type_from_order_item


SERVICE_CODE_PRESSING_BAG = "pressing_bag"
SERVICE_CODE_PRESSING_ARTICLE = "pressing_article"
SERVICE_CODE_REPASSAGE = "repassage"
SERVICE_CODE_RETOUCHE = "retouche_simple"
SERVICE_CODE_CORDONNERIE = "cordonnerie_standard"


LEGACY_CATEGORY_TO_V2_SERVICE_CODE = {
    "lavage-repassage": SERVICE_CODE_PRESSING_ARTICLE,
    "couettes-couvertures": SERVICE_CODE_PRESSING_ARTICLE,
    "repassage-seul": SERVICE_CODE_REPASSAGE,
    "retouche": SERVICE_CODE_RETOUCHE,
    "cordonnerie": SERVICE_CODE_CORDONNERIE,
}


CANONICAL_ITEM_TYPE_TO_V2_SERVICE_CODE = {
    "pressing": SERVICE_CODE_PRESSING_ARTICLE,
    "retouche": SERVICE_CODE_RETOUCHE,
    "cordonnerie": SERVICE_CODE_CORDONNERIE,
    "chaussures": SERVICE_CODE_CORDONNERIE,
}


class ServiceResolutionError(ValueError):
    """Erreur de résolution métier d'une commande vers un service V2."""


class AmbiguousServiceResolutionError(ServiceResolutionError):
    """Plusieurs services V2 incompatibles sont détectés sur la commande."""


class ServiceCatalogResolutionError(ServiceResolutionError):
    """Le code métier est résolu mais le catalogue V2 ne peut pas le fournir."""


def _normalize(value) -> str:
    return str(value or "").strip().lower()


def _load_order_items(order):
    """
    Charge les OrderItem en conservant, lorsque possible, les relations
    ServiceItem -> ServiceCategory nécessaires à la résolution legacy.
    """
    manager = getattr(order, "items", None)

    if manager is None:
        return []

    try:
        return list(
            manager
            .select_related("service__category")
            .all()
        )
    except (AttributeError, TypeError):
        try:
            return list(manager.all())
        except (AttributeError, TypeError):
            return list(manager)


def _resolve_service_code_for_item(item):
    """
    Résout UNE ligne OrderItem vers un code Service V2.

    Priorité locale :
    1. catégorie legacy connue liée à cette ligne ;
    2. sinon ré-inférence canonique de cette ligne.

    Cette priorité ligne par ligne évite deux erreurs :
    - une catégorie legacy fiable ne doit pas être contredite par
      une désignation générique de la même ligne ;
    - une catégorie legacy présente sur une ligne ne doit pas masquer
      un service différent détecté sur une autre ligne non liée.
    """
    service = getattr(item, "service", None)
    category = (
        getattr(service, "category", None)
        if service is not None
        else None
    )

    category_slug = _normalize(
        getattr(category, "slug", None)
        if category is not None
        else None
    )

    legacy_code = LEGACY_CATEGORY_TO_V2_SERVICE_CODE.get(
        category_slug
    )

    if legacy_code:
        return legacy_code

    canonical_type = _normalize(
        infer_service_type_from_order_item(item)
    )

    return CANONICAL_ITEM_TYPE_TO_V2_SERVICE_CODE.get(
        canonical_type
    )


def _resolve_codes_from_items(items):
    """
    Agrège les familles métier détectées dans les lignes d'une commande.

    Les codes sont :
    - dédupliqués ;
    - conservés dans l'ordre de première apparition ;
    - issus exclusivement du resolver canonique ligne par ligne.
    """
    resolved_codes = []
    seen_codes = set()

    for item in items:
        code = _resolve_service_code_for_item(item)

        if code and code not in seen_codes:
            seen_codes.add(code)
            resolved_codes.append(code)

    return tuple(resolved_codes)


def _resolve_from_items(items):
    """
    Compatibilité avec le contrat historique mono-service.

    Une seule famille métier -> service V2 résolu.
    Plusieurs familles -> ambiguïté explicite.
    Aucun signal -> None.
    """
    resolved_codes = _resolve_codes_from_items(items)

    if len(resolved_codes) > 1:
        raise AmbiguousServiceResolutionError(
            "Commande ambiguë : plusieurs lignes correspondent "
            "à des services V2 différents : "
            f"{list(resolved_codes)}."
        )

    if resolved_codes:
        return resolved_codes[0]

    return None


def resolve_v2_service_codes_for_order(order) -> tuple[str, ...]:
    """
    Résout une Order legacy vers UNE OU PLUSIEURS familles Service V2.

    Contrat multiservice :
    - bag reste volontairement mono-service pressing_bag ;
    - item peut produire plusieurs familles métier ;
    - les doublons sont supprimés ;
    - l'ordre de première apparition des familles est conservé ;
    - aucun signal exploitable conserve le fallback pressing_article.

    Cette fonction ne consulte volontairement PAS services.Service.
    Elle résout uniquement les codes métier canoniques.
    """
    if order is None:
        raise ServiceResolutionError(
            "Une commande est requise pour résoudre les services V2."
        )

    pricing_mode = _normalize(
        getattr(order, "pricing_mode", None)
    ) or "bag"

    if pricing_mode == "bag":
        return (SERVICE_CODE_PRESSING_BAG,)

    if pricing_mode != "item":
        raise ServiceResolutionError(
            "Mode de tarification non supporté pour la résolution V2 : "
            f"{pricing_mode!r}."
        )

    items = _load_order_items(order)
    resolved_codes = _resolve_codes_from_items(items)

    if resolved_codes:
        return resolved_codes

    return (SERVICE_CODE_PRESSING_ARTICLE,)


def resolve_v2_service_code_for_order(order) -> str:
    """
    Résout une orders.Order legacy vers UN code Service V2.

    Cette API conserve volontairement le contrat historique mono-service :
    lorsqu'une commande contient plusieurs familles canoniques, elle reste
    ambiguë pour ce resolver singulier.

    Les nouveaux flux multiservices doivent utiliser
    resolve_v2_service_codes_for_order().
    """
    resolved_codes = resolve_v2_service_codes_for_order(order)

    if len(resolved_codes) > 1:
        raise AmbiguousServiceResolutionError(
            "Commande ambiguë : plusieurs lignes correspondent "
            "à des services V2 différents : "
            f"{list(resolved_codes)}."
        )

    return resolved_codes[0]


def resolve_v2_services_for_order(order):
    """
    Résout une Order vers les objets Service V2 actifs correspondant
    à toutes les familles métier canoniques détectées.

    Garanties :
    - s'appuie sur resolve_v2_service_codes_for_order() ;
    - conserve exactement l'ordre des codes résolus ;
    - ne retourne aucun doublon ;
    - exige que TOUS les Services correspondants existent et soient actifs ;
    - ne crée aucune ServiceExecution.
    """
    from services.models import Service

    service_codes = resolve_v2_service_codes_for_order(order)

    services_by_code = {
        service.code: service
        for service in Service.objects.filter(
            code__in=service_codes,
            is_active=True,
        )
    }

    missing_codes = tuple(
        code
        for code in service_codes
        if code not in services_by_code
    )

    if missing_codes:
        raise ServiceCatalogResolutionError(
            "Services V2 actifs introuvables pour les codes résolus : "
            f"{list(missing_codes)}."
        )

    return tuple(
        services_by_code[code]
        for code in service_codes
    )


def resolve_v2_service_for_order(order):
    """
    Résout la commande vers l'objet services.Service actif.

    La séparation avec resolve_v2_service_code_for_order() est
    intentionnelle :
    - le code métier peut être résolu sans catalogue V2 ;
    - la matérialisation opérationnelle exige, elle, un Service V2
      réellement présent et actif.
    """
    from services.models import Service

    service_code = resolve_v2_service_code_for_order(order)

    try:
        return Service.objects.get(
            code=service_code,
            is_active=True,
        )
    except Service.DoesNotExist as exc:
        raise ServiceCatalogResolutionError(
            "Service V2 actif introuvable pour le code résolu "
            f"{service_code!r}."
        ) from exc
