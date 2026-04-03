# orders/utils/settings_loader.py

from decimal import Decimal
from dataclasses import dataclass

from orders.config_models import (
    GlobalPricingSettings,
    WorkflowSettings,
    AssignmentSettings,
)


@dataclass
class PricingConfig:
    """
    Wrapper "propre" autour de GlobalPricingSettings pour faciliter les calculs.
    """

    # Service FAGNI
    service_fee_percent: Decimal
    service_fee_min: Decimal
    laundry_commission_percent: Decimal
    rounding_step: int
    apply_service_on_delivery: bool

    # Livraison
    delivery_min_fee: Decimal
    delivery_price_per_km: Decimal
    delivery_fixed_fee: Decimal
    delivery_free_threshold: Decimal

    # Express
    express_extra_fixed: Decimal
    express_extra_percent: Decimal
    express_extra_target: str
    express_affects_customer_price: bool

    # Répartition logistique
    driver_share_ratio: Decimal
    driver_min_fee: Decimal
    logistic_margin_ratio: Decimal

    # TVA
    vat_rate: Decimal
    apply_vat_on_service_only: bool

    @classmethod
    def from_model(cls, obj: GlobalPricingSettings) -> "PricingConfig":
        return cls(
            # Service FAGNI
            service_fee_percent=Decimal(str(obj.service_fee_percent or 0)),
            service_fee_min=Decimal(str(obj.service_fee_min or 0)),
            laundry_commission_percent=Decimal(str(getattr(obj, "laundry_commission_percent", 0) or 0)),
            rounding_step=int(obj.rounding_step or 0),
            apply_service_on_delivery=bool(obj.apply_service_on_delivery),
            # Livraison
            delivery_min_fee=Decimal(str(obj.delivery_min_fee or 0)),
            delivery_price_per_km=Decimal(str(obj.delivery_price_per_km or 0)),
            delivery_fixed_fee=Decimal(str(obj.delivery_fixed_fee or 0)),
            delivery_free_threshold=Decimal(str(obj.delivery_free_threshold or 0)),
            # Express
            express_extra_fixed=Decimal(str(obj.express_extra_fixed or 0)),
            express_extra_percent=Decimal(str(obj.express_extra_percent or 0)),
            express_extra_target=obj.express_extra_target or "LAUNDRY",
            express_affects_customer_price=bool(obj.express_affects_customer_price),
            # Répartition logistique
            driver_share_ratio=Decimal(str(obj.driver_share_ratio or 0)),
            driver_min_fee=Decimal(str(obj.driver_min_fee or 0)),
            logistic_margin_ratio=Decimal(str(obj.logistic_margin_ratio or 0)),
            # TVA
            vat_rate=Decimal(str(obj.vat_rate or 0)),
            apply_vat_on_service_only=bool(obj.apply_vat_on_service_only),
        )


def get_pricing_settings() -> PricingConfig:
    """
    Retourne la config de pricing FAGNI, prête à l'emploi.
    """
    obj = GlobalPricingSettings.get_solo()
    return PricingConfig.from_model(obj)


def get_workflow_settings() -> WorkflowSettings:
    return WorkflowSettings.get_solo()


def get_assignment_settings() -> AssignmentSettings:
    return AssignmentSettings.get_solo()
