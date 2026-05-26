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


# ══════════════════════════════════════════════════════
# AUTO SCORING ENGINE v1 — FAGNI
# ══════════════════════════════════════════════════════

PENALTIES = {
    'retard_48h':      -10,
    'litige_client':   -15,
    'refus_commande':   -5,
    'scelle_manquant': -20,
}

BONUSES = {
    'semaine_sans_incident': +10,
    'commande_parfaite':      +5,
}

def _clamp(value, min_val=0, max_val=100):
    return max(min_val, min(max_val, value))

def apply_partner_penalty(partner, reason, order=None):
    """
    Applique une pénalité au Partner Score.
    Enregistre dans PartnerScoreHistory.
    """
    from django.utils import timezone
    from partners.models import PartnerScoreHistory

    delta = PENALTIES.get(reason, -5)
    previous = partner.partner_score
    partner.partner_score = _clamp(previous + delta)
    partner.update_level()
    partner.score_updated_at = timezone.now()
    partner.save(update_fields=['partner_score', 'level', 'score_updated_at'])

    PartnerScoreHistory.objects.create(
        partner       = partner,
        score         = partner.partner_score,
        level         = partner.level,
        score_delai   = partner.score_delai,
        score_litiges = partner.score_litiges,
        score_dispo   = partner.score_dispo,
        score_refus   = partner.score_refus,
    )

    return {
        'partner':   partner.name,
        'reason':    reason,
        'delta':     delta,
        'previous':  previous,
        'new_score': partner.partner_score,
        'level':     partner.level,
        'badge':     get_partner_badge(partner),
    }


def apply_partner_bonus(partner, reason, order=None):
    """
    Applique un bonus au Partner Score.
    Enregistre dans PartnerScoreHistory.
    """
    from django.utils import timezone
    from partners.models import PartnerScoreHistory

    delta = BONUSES.get(reason, +5)
    previous = partner.partner_score
    partner.partner_score = _clamp(previous + delta)
    partner.update_level()
    partner.score_updated_at = timezone.now()
    partner.save(update_fields=['partner_score', 'level', 'score_updated_at'])

    PartnerScoreHistory.objects.create(
        partner       = partner,
        score         = partner.partner_score,
        level         = partner.level,
        score_delai   = partner.score_delai,
        score_litiges = partner.score_litiges,
        score_dispo   = partner.score_dispo,
        score_refus   = partner.score_refus,
    )

    return {
        'partner':   partner.name,
        'reason':    reason,
        'delta':     delta,
        'previous':  previous,
        'new_score': partner.partner_score,
        'level':     partner.level,
        'badge':     get_partner_badge(partner),
    }


def get_partner_badge(partner):
    """Retourne le badge couleur OPS selon le score."""
    score = partner.partner_score
    if score >= 80:
        return {'emoji': '🟢', 'label': 'Excellent', 'color': '#1B7A4E'}
    elif score >= 60:
        return {'emoji': '🟡', 'label': 'Correct',   'color': '#C9A84C'}
    elif score >= 40:
        return {'emoji': '🟠', 'label': 'Attention', 'color': '#E67E22'}
    else:
        return {'emoji': '🔴', 'label': 'Critique',  'color': '#C0392B'}
