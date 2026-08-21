from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from payments.models import CustomerCharge


def _money(value):
    """
    Normalise un montant monétaire FAGNI à 2 décimales.
    """
    return Decimal(str(value or "0")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


@transaction.atomic
def apply_customer_charge(
    *,
    customer,
    charge_type,
    amount,
    reason="",
    order=None,
    currency="XOF",
    idempotency_key=None,
):
    """
    Crée une créance client FAGNI de manière idempotente.

    Règles :
    - customer obligatoire ;
    - amount strictement positif ;
    - si order est fourni, il doit appartenir au customer ;
    - pour une annulation tardive, la clé canonique est :
      late_cancellation:order:<order_id>
    - rejouer la même opération retourne la créance existante ;
    - aucune écriture wallet n'est créée ici ;
    - aucune modification du total de la commande.
    """

    if customer is None or getattr(customer, "pk", None) is None:
        raise ValueError(
            "apply_customer_charge : customer persisté obligatoire."
        )

    amount = _money(amount)

    if amount <= 0:
        raise ValueError(
            "apply_customer_charge : amount doit être strictement positif."
        )

    valid_charge_types = {
        value
        for value, _label in CustomerCharge.ChargeType.choices
    }

    if charge_type not in valid_charge_types:
        raise ValueError(
            f"apply_customer_charge : charge_type invalide : {charge_type}"
        )

    if order is not None:
        if getattr(order, "pk", None) is None:
            raise ValueError(
                "apply_customer_charge : order doit être persistée."
            )

        if getattr(order, "customer_id", None) != customer.pk:
            raise ValueError(
                "apply_customer_charge : la commande "
                "n'appartient pas au client."
            )

    if not idempotency_key:
        if (
            charge_type
            == CustomerCharge.ChargeType.LATE_CANCELLATION
        ):
            if order is None:
                raise ValueError(
                    "apply_customer_charge : "
                    "late_cancellation exige une commande."
                )

            idempotency_key = (
                f"late_cancellation:order:{order.pk}"
            )
        else:
            raise ValueError(
                "apply_customer_charge : idempotency_key obligatoire "
                "pour ce type de créance."
            )

    idempotency_key = str(idempotency_key).strip()

    if not idempotency_key:
        raise ValueError(
            "apply_customer_charge : idempotency_key vide."
        )

    charge, created = CustomerCharge.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "customer": customer,
            "order": order,
            "charge_type": charge_type,
            "amount": amount,
            "currency": currency,
            "status": CustomerCharge.Status.DUE,
            "reason": str(reason or "").strip(),
        },
    )

    if not created:
        if charge.customer_id != customer.pk:
            raise ValueError(
                "apply_customer_charge : collision de clé "
                "d'idempotence avec un autre client."
            )

        if charge.order_id != getattr(order, "pk", None):
            raise ValueError(
                "apply_customer_charge : collision de clé "
                "d'idempotence avec une autre commande."
            )

        if charge.charge_type != charge_type:
            raise ValueError(
                "apply_customer_charge : collision de clé "
                "d'idempotence avec un autre type de créance."
            )

    return charge, created
