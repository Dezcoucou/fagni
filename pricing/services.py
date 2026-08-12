from decimal import Decimal

from django.db import transaction

from pricing.models import PricingRule, PriceQuote


def _validate_service_execution_order(*, order, service_execution):
    """
    Garantit qu'un PriceQuote ne peut pas être rattaché à une
    ServiceExecution appartenant à une autre commande.

    Pendant la phase de strangulation, service_execution peut être None.
    """
    if service_execution is None:
        return

    execution_order_id = getattr(
        service_execution,
        "order_id",
        None,
    )
    order_id = getattr(
        order,
        "id",
        None,
    )

    if not order_id:
        raise ValueError(
            "Impossible de créer un PriceQuote : commande non persistée."
        )

    if execution_order_id != order_id:
        raise ValueError(
            "ServiceExecution incompatible : "
            "l'exécution de service et le PriceQuote doivent appartenir "
            "à la même commande."
        )


@transaction.atomic
def create_estimated_quote(
    *,
    order,
    service_execution=None,
    notes="",
):
    """
    Crée un devis estimatif.

    Compatibilité :
    - legacy : order seul reste accepté ;
    - multiservices : service_execution peut être fourni.

    Aucune ServiceExecution n'est créée implicitement ici.
    """
    _validate_service_execution_order(
        order=order,
        service_execution=service_execution,
    )

    logistics_fee = Decimal("0")
    service_fee = Decimal("0")
    subtotal_amount = Decimal("0")
    discount_amount = Decimal("0")

    # Exemple simple de logique initiale :
    # - si une règle fixed_fee existe sur collecte_livraison, on l'utilise
    # - sinon zéro
    fixed_rule = (
        PricingRule.objects.filter(
            rule_type="fixed_fee",
            is_active=True,
        )
        .order_by("priority", "id")
        .first()
    )
    if fixed_rule:
        logistics_fee = Decimal(str(fixed_rule.value))

    total_amount = subtotal_amount + logistics_fee + service_fee - discount_amount

    quote = PriceQuote.objects.create(
        order=order,
        service_execution=service_execution,
        quote_type="estimated",
        subtotal_amount=subtotal_amount,
        logistics_fee=logistics_fee,
        service_fee=service_fee,
        discount_amount=discount_amount,
        total_amount=total_amount,
        currency="XOF",
        is_final=False,
        notes=notes or "Devis estimatif initial V2",
    )
    return quote


@transaction.atomic
def finalize_quote(*, quote, notes=""):
    """
    Finalise un PriceQuote puis réévalue sa ServiceExecution éventuelle.

    Garanties :
    - legacy : un devis sans ServiceExecution reste finalisable ;
    - aucune ServiceExecution n'est créée implicitement ;
    - la clôture éventuelle passe uniquement par
      complete_service_execution_if_ready().
    """
    quote.quote_type = "final"
    quote.is_final = True

    update_fields = [
        "quote_type",
        "is_final",
        "updated_at",
    ]

    if notes:
        quote.notes = (
            (quote.notes + "\n" + notes).strip()
            if quote.notes
            else notes
        )
        update_fields.append("notes")

    quote.save(update_fields=update_fields)

    if quote.service_execution_id is not None:
        from services.services import complete_service_execution_if_ready

        complete_service_execution_if_ready(
            service_execution=quote.service_execution,
            note=(
                "ServiceExecution réévaluée après finalisation "
                f"du PriceQuote #{quote.id}."
            ),
        )

    return quote
