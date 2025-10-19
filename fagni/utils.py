import math
from typing import Optional, List, Tuple
from django.db import transaction
from .models import Partenaire, Commande, AffectationLog


def _haversine_km(lat1, lon1, lat2, lon2):
    """Distance en km entre deux points lat/lon."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _score_partenaire(p: Partenaire, cmd: Commande,
                      w_dist=1.0, w_charge=2.0, penal_hors_zone=3.0) -> Tuple[float, float, bool]:
    """
    Score multi-critères :
      - distance (km) pondérée
      - charge relative (charge/capacité) pondérée
      - pénalité si hors de la même zone
    Score le plus FAIBLE = meilleur.
    """
    # Distance (avec fallback à 50 km si coordonnées manquantes pour éviter l'avantage)
    d = _haversine_km(cmd.latitude, cmd.longitude, p.latitude, p.longitude)
    distance_km = d if d is not None else 50.0

    # Charge relative
    charge_rel = (p.charge_du_jour / max(1, p.capacite_jour))

    # Même zone ?
    same_zone = False
    if cmd.zone and p.zone:
        same_zone = (cmd.zone.strip().lower() == p.zone.strip().lower())

    # Pénalité hors zone
    penalty = 0.0 if same_zone else penal_hors_zone

    score = (distance_km * w_dist) + (charge_rel * w_charge) + penalty
    return score, distance_km, same_zone


@transaction.atomic
def assigner_partenaire_automatique(commande: Commande) -> Optional[Partenaire]:
    """
    Affecte automatiquement un partenaire selon :
      1) actifs + disponibles + capacité + type_service identique ;
      2) score (distance + charge + zone) le plus FAIBLE ;
      3) verrou transactionnel pour éviter les doubles assignations.
    Si aucun candidat : commande marquée a_dispatcher=True + log.
    """
    # Pré-filtrage base
    qs = Partenaire.objects.filter(
        actif=True,
        disponible=True,
        type_service=commande.type_service,
    )

    candidats: List[Partenaire] = [p for p in qs if p.a_de_la_capacite]
    if not candidats:
        commande.a_dispatcher = True
        commande.save(update_fields=["a_dispatcher"])
        AffectationLog.objects.create(
            commande=commande,
            message="Aucun partenaire disponible (pré-filtre).",
        )
        return None

    # Calcul des scores pour tous
    scored: List[Tuple[float, float, bool, Partenaire]] = []
    for p in candidats:
        score, dist_km, same_zone = _score_partenaire(p, commande)
        scored.append((score, dist_km, same_zone, p))

    # Tri croissant par score
    scored.sort(key=lambda t: t[0])

    # Tentative d'assignation en verrouillant la ligne partenaire
    for score, dist_km, same_zone, p in scored:
        # recharger avec lock
        p_locked = Partenaire.objects.select_for_update().get(pk=p.pk)
        if not p_locked.a_de_la_capacite:
            continue
        p_locked.charge_du_jour += 1
        p_locked.save(update_fields=["charge_du_jour"])

        commande.partenaire = p_locked
        commande.a_dispatcher = False
        commande.save(update_fields=["partenaire", "a_dispatcher"])

        AffectationLog.objects.create(
            commande=commande,
            partenaire=p_locked,
            score=score,
            distance_km=dist_km,
            meme_zone=same_zone,
            message="Affectation automatique OK.",
        )
        return p_locked

    # Si tout le monde est devenu plein entre temps
    commande.a_dispatcher = True
    commande.save(update_fields=["a_dispatcher"])
    AffectationLog.objects.create(
        commande=commande,
        message="Tous les candidats sont pleins (course).",
    )
    return None