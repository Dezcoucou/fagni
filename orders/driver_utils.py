from partners.models import DeliveryPartner

def resolve_driver(request):
    """
    Identifie le livreur connecté à partir de :
    - driver_id passé dans l'URL
    - OU du dernier driver utilisé en session (si tu veux activer ça plus tard)

    Retourne :
    - driver (DeliveryPartner instance ou None)
    - error_message (texte ou None)
    """

    driver_id = request.GET.get("driver_id")

    if not driver_id:
        return None, "Aucun livreur sélectionné (driver_id manquant)."

    try:
        driver = DeliveryPartner.objects.get(id=driver_id)
        return driver, None
    except DeliveryPartner.DoesNotExist:
        return None, f"Livreur introuvable (ID={driver_id})."
