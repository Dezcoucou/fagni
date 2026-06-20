
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

@api_view(['GET'])
@permission_classes([AllowAny])
def api_config(request):
    from orders.config_models import GlobalPricingSettings
    cfg = GlobalPricingSettings.get_solo()
    return Response({
        'prix_article_fcfa': cfg.prix_article_fcfa,
        'service_fee_percent': float(cfg.service_fee_percent),
        'service_fee_min': cfg.service_fee_min,
        'delivery_fixed_fee': float(cfg.delivery_fixed_fee),
        'driver_amount_per_leg': cfg.driver_amount_per_leg,
        'laundry_commission_percent': float(cfg.laundry_commission_percent),
        'wave_business_number': cfg.wave_business_number,
        'whatsapp_ops_number': cfg.whatsapp_ops_number,
    })
