"""API Opérateur FAGNI — Dashboard de pilotage"""
import jwt
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.utils import timezone


def _check_ops(request):
    token = request.headers.get('Authorization','').replace('Bearer ','')
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    if not payload.get('ops'):
        raise Exception('Non autorisé')
    return payload


@api_view(['POST'])
@permission_classes([AllowAny])
def ops_login(request):
    """POST /api/ops/login/ — {password} → JWT ops"""
    password = request.data.get('password','').strip()
    ops_password = getattr(settings, 'OPS_PASSWORD', 'fagni2025')
    if password != ops_password:
        return Response({'error': 'Mot de passe incorrect'}, status=401)
    token = jwt.encode({'ops': True, 'name': 'Opérateur FAGNI'}, settings.SECRET_KEY, algorithm='HS256')
    return Response({'access': token})


@api_view(['GET'])
@permission_classes([AllowAny])
def ops_dashboard(request):
    """GET /api/ops/dashboard/ — vue globale toutes commandes"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order
    from partners.models import LaundryPartner

    # Filtres
    status = request.GET.get('status', '')
    partner_id = request.GET.get('partner', '')

    qs = Order.objects.select_related('customer', 'laundry_partner').order_by('-created_at')
    if status:
        qs = qs.filter(status=status)
    if partner_id:
        qs = qs.filter(laundry_partner_id=partner_id)

    orders = []
    for o in qs[:100]:
        phone = o.customer.phone if o.customer else ''
        partner = o.laundry_partner

        # Générer lien WhatsApp client
        client_phone = phone.replace(' ','').replace('-','')
        if client_phone and not client_phone.startswith('+'):
            client_phone = '225' + client_phone.lstrip('0')

        wa_client = ''
        if client_phone:
            msg = f"Bonjour ! Votre commande FAGNI {o.code} est prête. Le livreur arrive bientôt."
            wa_client = f"https://wa.me/{client_phone}?text={msg.replace(' ','%20')}"

        # Générer lien WhatsApp partenaire
        wa_partner = ''
        if partner and partner.phone:
            p_phone = partner.phone.replace(' ','').replace('-','')
            if not p_phone.startswith('+'):
                p_phone = '225' + p_phone.lstrip('0')
            bag = {'small':'Petit sac','medium':'Sac moyen','large':'Grand sac'}.get(o.bag_size or '','Sac')
            msg2 = f"Bonjour {partner.name} ! Nouvelle commande FAGNI {o.code} - {bag}. Le livreur arrive."
            wa_partner = f"https://wa.me/{p_phone}?text={msg2.replace(' ','%20')}"

        orders.append({
            'id':             o.id,
            'code':           o.code or str(o.id),
            'status':         o.status,
            'payment_status': o.payment_status,
            'service_type':   o.service_type or 'pressing',
            'total':          float(o.total or 0),
            'bag_size':       o.bag_size or '',
            'customer_name':  o.customer.name if o.customer else '—',
            'customer_phone': o.customer.phone if o.customer else '—',
            'partner_name':   partner.name if partner else None,
            'partner_id':     partner.id if partner else None,
            'wa_client':      wa_client,
            'wa_partner':     wa_partner,
            'created_at':     o.created_at.isoformat() if o.created_at else None,
        })

    # Stats globales
    all_orders = Order.objects.all()
    stats = {
        'total':       all_orders.count(),
        'pending':     all_orders.filter(status='pending').count(),
        'in_progress': all_orders.filter(status='in_progress').count(),
        'done':        all_orders.filter(status='done').count(),
        'unpaid':      all_orders.filter(payment_status='unpaid').count(),
        'revenue':     float(all_orders.filter(payment_status='paid').aggregate(
            t=__import__('django.db.models',fromlist=['Sum']).Sum('total'))['t'] or 0),
    }

    # Partenaires pour filtre
    partners = list(LaundryPartner.objects.filter(is_active=True).values('id','name','phone'))

    return Response({'orders': orders, 'stats': stats, 'partners': partners})


@api_view(['POST'])
@permission_classes([AllowAny])
def ops_assign_partner(request, order_id):
    """POST /api/ops/orders/<id>/assign/ — {partner_id}"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order
    from partners.models import LaundryPartner

    try:
        order = Order.objects.get(id=order_id)
        partner_id = request.data.get('partner_id')
        partner = LaundryPartner.objects.get(id=partner_id) if partner_id else None
        order.laundry_partner = partner
        order.save(update_fields=['laundry_partner', 'updated_at'])
        return Response({'success': True, 'partner': partner.name if partner else None})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def ops_update_status(request, order_id):
    """POST /api/ops/orders/<id>/status/ — {status}"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order
    ALLOWED = ['pending','in_progress','done','canceled','ready']
    try:
        order = Order.objects.get(id=order_id)
        new_status = request.data.get('status','')
        if new_status not in ALLOWED:
            return Response({'error': 'Statut invalide'}, status=400)
        order.status = new_status
        order.save(update_fields=['status','updated_at'])
        return Response({'success': True, 'status': new_status})
    except Exception as e:
        return Response({'error': str(e)}, status=400)
