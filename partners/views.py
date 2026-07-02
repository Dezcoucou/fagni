from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import LaundryPartner


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def partner_list(request):
    """Liste des partenaires avec leur score — pour OPS dashboard."""
    partners = LaundryPartner.objects.filter(is_active=True).values(
        "id", "name", "partner_type", "city", "wave_number",
        "partner_score", "level",
        "score_delai", "score_litiges", "score_dispo", "score_refus",
        "score_updated_at", "remuneration_collecte", "remuneration_livraison"
    )
    return Response(list(partners))


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_score(request, pk):
    """Met à jour les sous-scores et recalcule le Partner Score."""
    try:
        partner = LaundryPartner.objects.get(pk=pk)
    except LaundryPartner.DoesNotExist:
        return Response({"error": "Partenaire introuvable"}, status=404)

    fields = ["score_delai", "score_litiges", "score_dispo", "score_refus"]
    for field in fields:
        if field in request.data:
            try:
                val = int(request.data[field])
            except (TypeError, ValueError):
                return Response({"error": f"Valeur invalide pour {field}"}, status=400)
            setattr(partner, field, val)

    partner.recalculate_score()
    partner.score_updated_at = timezone.now()
    partner.save()

    return Response({
        "id":            partner.id,
        "name":          partner.name,
        "partner_score": partner.partner_score,
        "level":         partner.level,
        "score_delai":   partner.score_delai,
        "score_litiges": partner.score_litiges,
        "score_dispo":   partner.score_dispo,
        "score_refus":   partner.score_refus,
    })
