from decimal import Decimal, ROUND_HALF_UP
from mlm.models import ReferralCommission, WalletTransaction, MLMSettings, ReferralLink
import random
import string


def generate_mlm_commissions_for_order(order):
    """
    Génère les commissions MLM pour une commande PAYÉE :
    - Idempotent (ne recrée pas si déjà créé)
    - Plan (N1/N2/N3) via MLMSettings
    """
    # 1) Seulement si payé (source de vérité)
    if getattr(order, "payment_status", None) != "paid":
        return

    # 2) Idempotence : si déjà des commissions, on sort
    if ReferralCommission.objects.filter(order=order).exists():
        return

    # 3) Profil affilié du client
    try:
        link = ReferralLink.objects.get(customer=order.customer)
    except ReferralLink.DoesNotExist:
        return

    # 4) Upline (N1, N2, N3)
    upline = link.get_upline(levels=3)
    if not upline:
        return

    # 5) Paramétrage actif
    settings_obj = MLMSettings.get_active()

    # Base MLM = service FAGNI
    fee_base_decimal = Decimal(str(getattr(order, "service_fee", 0) or 0)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # Fallback métier si service_fee n'a pas encore été persisté
    if fee_base_decimal <= Decimal("0.00"):
        try:
            computed_fee = order.compute_service_fee()
            fee_base_decimal = Decimal(str(computed_fee or 0)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except Exception:
            fee_base_decimal = Decimal("0.00")

        # si on a réussi à recalculer, on resynchronise la commande
        if fee_base_decimal > Decimal("0.00") and hasattr(order, "service_fee"):
            try:
                order.service_fee = fee_base_decimal
                order.save(update_fields=["service_fee"])
            except Exception:
                try:
                    order.save()
                except Exception:
                    pass

    # Pas de commissions si la base service FAGNI reste trop faible
    min_fee = Decimal(str(getattr(settings_obj, "min_service_fee_for_commission", 0) or 0)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if fee_base_decimal < min_fee:
        return

    fee_base = int(fee_base_decimal)

    # 6) Pourcentages
    percent_levels = [
        settings_obj.level1_percent,
        settings_obj.level2_percent,
        settings_obj.level3_percent,
    ]

    # 7) Calcul des commissions
    for idx, sponsor_profile in enumerate(upline[:3]):
        percent = percent_levels[idx] if idx < len(percent_levels) else 0
        commission_decimal = (Decimal(fee_base) * percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        commission_amount = int(commission_decimal)

        if commission_amount <= 0:
            continue

        level = idx + 1

        # On crée la commission
        ReferralCommission.objects.create(
            beneficiary_profile=sponsor_profile,
            level=level,
            order=order,
            service_fee_base=fee_base,
            commission_percent=percent,
            commission_amount=commission_amount,
        )

        # On crédite le wallet
        WalletTransaction.objects.create(
            profile=sponsor_profile,
            type="mlm_commission",
            amount=commission_amount,
            order=order,
            description=f"Commission niveau {level} - commande {order.code}",
        )


def generate_unique_referral_code(prefix: str = "AFF") -> str:
    """
    Génère un code affilié unique du type AFF-XYZ123.
    On boucle tant que le code existe déjà.
    """
    from .models import ReferralLink

    while True:
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        code = f"{prefix}-{suffix}"
        if not ReferralLink.objects.filter(referral_code__iexact=code).exists():
            return code


def normalize_referral_code(raw_code: str | None) -> str | None:
    """
    Nettoie un code affilié saisi par un utilisateur :
    - strip espaces
    - uppercase
    Retourne None si vide.
    """
    if not raw_code:
        return None
    code = str(raw_code).strip()
    if not code:
        return None
    return code.upper()


def get_sponsor_profile_by_code(raw_code: str | None):
    """
    Retourne le profil MLM du parrain à partir d'un code affilié,
    ou None si le code est invalide / inexistant.
    """
    from .models import ReferralLink

    code = normalize_referral_code(raw_code)
    if not code:
        return None

    try:
        return ReferralLink.objects.get(referral_code__iexact=code)
    except ReferralLink.DoesNotExist:
        return None


def attach_customer_to_sponsor(customer, sponsor_code: str | None):
    """
    Rattache un client FAGNI à un parrain via son code affilié.

    - Si sponsor_code est vide ou invalide → on ne fait rien, on retourne None.
    - Si le client a déjà un profil MLM, on NE recrée pas un profil :
        - si pas de sponsor, on lui associe le parrain trouvé
        - sinon, on laisse tel quel (pour éviter de changer d'upline sauvagement)
    - Si le client n'a pas encore de profil MLM :
        - on crée un ReferralLink avec :
            * customer=client
            * sponsor = parrain trouvé
            * un code affilié unique (pour qu'il puisse à son tour parrainer)
    """
    from .models import ReferralLink

    sponsor = get_sponsor_profile_by_code(sponsor_code)
    if sponsor is None:
        # Code invalide ou parrain inexistant : on ne fait rien
        return None

    # Est-ce que le client a déjà un profil affilié ?
    try:
        profile = ReferralLink.objects.get(customer=customer)
        created = False
    except ReferralLink.DoesNotExist:
        profile = None
        created = True

    if created:
        # On crée un nouveau profil affilié pour ce client
        profile = ReferralLink.objects.create(
            customer=customer,
            sponsor=sponsor,
            referral_code=generate_unique_referral_code(prefix="AFF"),
            actor_type="client",
        )
        return profile

    # Le client avait déjà un profil MLM
    # S'il n'avait pas de sponsor, on peut le compléter
    if profile.sponsor is None:
        profile.sponsor = sponsor
        profile.save(update_fields=["sponsor"])

    # S'il avait déjà un sponsor, on NE TOUCHE PAS (sécurité business)
    return profile
