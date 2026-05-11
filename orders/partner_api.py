"""API Partenaire FAGNI — Blanchisserie"""
import jwt
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


def _get_partner(request):
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    from partners.models import LaundryPartner
    return LaundryPartner.objects.get(id=payload['pid'])


@api_view(['POST'])
@permission_classes([AllowAny])
def partner_login(request):
    """POST /api/partner/login/ — {phone} → JWT"""
    phone = (request.data.get('phone') or '').strip()
    if not phone:
        return Response({'error': 'Numéro requis'}, status=400)
    try:
        from partners.models import LaundryPartner
        partner = LaundryPartner.objects.get(phone=phone, is_active=True)
    except Exception:
        return Response({'error': 'Partenaire non trouvé'}, status=404)

    token = jwt.encode(
        {'pid': partner.id, 'name': partner.name},
        settings.SECRET_KEY, algorithm='HS256'
    )
    return Response({
        'access': token,
        'partner': {'id': partner.id, 'name': partner.name, 'phone': partner.phone}
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def partner_orders(request):
    """GET /api/partner/orders/ — commandes assignées"""
    try:
        partner = _get_partner(request)
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order
    status_filter = request.GET.get('status', '')
    qs = Order.objects.filter(laundry_partner=partner).order_by('-created_at')
    if status_filter:
        qs = qs.filter(status=status_filter)

    orders = []
    for o in qs[:50]:
        orders.append({
            'id':             o.id,
            'code':           o.code or str(o.id),
            'status':         o.status,
            'payment_status': o.payment_status,
            'service_type':   o.service_type or 'pressing',
            'total':          float(o.total or 0),
            'pickup_address': o.pickup_address or '',
            'bag_size':       o.bag_size or '',
            'created_at':     o.created_at.isoformat() if o.created_at else None,
            'customer_name':  o.customer.name if o.customer else '—',
            'customer_phone': o.customer.phone if o.customer else '—',
        })

    stats = {
        'total':       qs.count(),
        'pending':     qs.filter(status='pending').count(),
        'in_progress': qs.filter(status='in_progress').count(),
        'done':        qs.filter(status='done').count(),
    }
    return Response({'orders': orders, 'stats': stats, 'partner': partner.name})


@api_view(['POST'])
@permission_classes([AllowAny])
def partner_update_status(request, order_id):
    """POST /api/partner/orders/<id>/status/ — {status}"""
    try:
        partner = _get_partner(request)
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order
    try:
        order = Order.objects.get(id=order_id, laundry_partner=partner)
    except Exception:
        return Response({'error': 'Commande non trouvée'}, status=404)

    new_status = request.data.get('status', '').strip()
    ALLOWED = ['in_progress', 'done', 'pending']
    if new_status not in ALLOWED:
        return Response({'error': f'Statut invalide. Choix: {ALLOWED}'}, status=400)

    order.status = new_status
    order.save(update_fields=['status', 'updated_at'])
    return Response({'success': True, 'status': new_status, 'code': order.code})
