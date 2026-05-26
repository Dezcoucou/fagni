from django.utils import timezone
from datetime import timedelta

from orders.models import Order


def recompute_partner_score(partner):
    """
    Recalcule le Partner Score FAGNI.
    """

    now = timezone.now()
    since = now - timedelta(days=7)

    orders = Order.objects.filter(
        laundry_partner=partner,
        created_at__gte=since
    )

    # =========================
    # SCORE DÉLAI /40
    # =========================

    delayed = 0
    for o in orders.filter(status='done'):
        if o.created_at and o.updated_at:
            duration = o.updated_at - o.created_at
            if duration > timedelta(hours=48):
                delayed += 1

    if delayed == 0:
        score_delai = 40
    elif delayed == 1:
        score_delai = 30
    elif delayed == 2:
        score_delai = 20
    else:
        score_delai = 10

    # =========================
    # SCORE LITIGES /30
    # =========================

    litiges = orders.filter(
        notes__icontains='LITIGE:'
    ).count()

    if litiges == 0:
        score_litiges = 30
    elif litiges == 1:
        score_litiges = 20
    elif litiges == 2:
        score_litiges = 10
    else:
        score_litiges = 0

    # =========================
    # SCORE DISPO /20
    # =========================

    score_dispo = 20 if partner.is_active else 0

    # =========================
    # SCORE REFUS /10
    # =========================

    refus = orders.filter(
        status='canceled'
    ).count()

    if refus == 0:
        score_refus = 10
    elif refus == 1:
        score_refus = 5
    else:
        score_refus = 0

    total = (
        score_delai +
        score_litiges +
        score_dispo +
        score_refus
    )

    partner.score_delai = score_delai
    partner.score_litiges = score_litiges
    partner.score_dispo = score_dispo
    partner.score_refus = score_refus
    partner.partner_score = total
    partner.score_updated_at = now

    partner.recalculate_score()

    partner.save(
        update_fields=[
            'score_delai',
            'score_litiges',
            'score_dispo',
            'score_refus',
            'partner_score',
            'level',
            'score_updated_at',
        ]
    )

    # Sauvegarder dans l'historique
    from partners.models import PartnerScoreHistory
    PartnerScoreHistory.objects.create(
        partner       = partner,
        score         = total,
        level         = partner.level,
        score_delai   = score_delai,
        score_litiges = score_litiges,
        score_dispo   = score_dispo,
        score_refus   = score_refus,
    )

    return partner


def get_partner_payment_delay_days(partner):
    """
    Délai de paiement partenaire selon niveau FAGNI.
    Gold   : 2 jours
    Silver : 3 jours
    Bronze : 7 jours
    """
    level = getattr(partner, "level", "bronze") or "bronze"

    if level == "gold":
        return 2
    if level == "silver":
        return 3
    return 7


def get_partner_payment_label(partner):
    days = get_partner_payment_delay_days(partner)
    if days == 2:
        return "Paiement sous 48h"
    if days == 3:
        return "Paiement sous 3 jours"
    return "Paiement hebdomadaire"
