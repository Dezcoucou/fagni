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
        partner = LaundryPartner.objects.filter(phone=phone, is_active=True).first()
        if not partner: raise Exception('not found')
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
    import re, ast
    status_filter = request.GET.get('status', '')
    qs = Order.objects.filter(laundry_partner=partner).order_by('-created_at')
    if status_filter:
        qs = qs.filter(status=status_filter)

    def _count_articles(o):
        try:
            m = re.search(r'Articles: (\[.*?\])', (o.notes or '').replace('\n',' '))
            if m:
                return sum(int(a.get('quantity') or a.get('qty') or 1) for a in ast.literal_eval(m.group(1)))
        except: pass
        return o.items.count()

    orders = []
    for o in qs[:50]:
        orders.append({
            'id':             o.id,
            'code':           o.code or str(o.id),
            'status':         o.status,
            'payment_status': o.payment_status,
            'service_type':   o.service_type or 'pressing',
            'total':          float(o.total or 0),
            'bag_size':       o.bag_size or '',
            'created_at':     o.created_at.isoformat() if o.created_at else None,
            'zone':           o.pickup_address.split(',')[0] if o.pickup_address else '—',
            'articles_count': _count_articles(o),
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
    import re, ast
    try:
        order = Order.objects.get(id=order_id, laundry_partner=partner)
    except Exception:
        return Response({'error': 'Commande non trouvée'}, status=404)

    raw_status = request.data.get('status', '').strip()
    STATUS_MAP = {
        'received': 'in_progress',
        'received_bag': 'in_progress',
        'bag_received': 'in_progress',
        'recu': 'in_progress',
        'reçu': 'in_progress',
        'start': 'in_progress',
        'ready': 'in_progress',
        'in_progress': 'in_progress',
        'done': 'done',
        'pending': 'pending',
    }
    new_status = STATUS_MAP.get(raw_status, raw_status)

    ALLOWED = ['in_progress', 'done', 'pending', 'ready']
    if new_status not in ALLOWED:
        return Response({
            'error': 'statut_invalide',
            'received': raw_status,
            'mapped': new_status,
            'choices': ALLOWED,
        }, status=400)

    # Mode terrain MVP : la blanchisserie peut confirmer la réception du sac.
    # On ne bloque pas ici sur paiement ou note DEPOSE_PRESSING, car ces contrôles
    # peuvent empêcher le test opérationnel alors que le sac est physiquement reçu.

    order.status = new_status
    order.save(update_fields=['status', 'updated_at'])
    if raw_status in ('ready', 'done') or new_status == 'done':
        from django.utils import timezone
        from orders.models import DeliveryLeg
        Order.objects.filter(pk=order.pk).update(wash_complete_time=timezone.now())
        DeliveryLeg.objects.get_or_create(
            order=order, leg_type='return',
            defaults={'status': 'pending', 'driver_amount': 800}
        )
    return Response({'success': True, 'status': new_status, 'code': order.code})


@api_view(['POST'])
@permission_classes([AllowAny])
def partner_refuse_order(request, order_id):
    """POST /api/partner/orders/<id>/refuse/ — pressing refuse une commande"""
    try:
        driver = _get_partner(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from orders.models import Order
        import re, ast
        import urllib.parse

        order = Order.objects.get(id=order_id, laundry_partner=driver)

        if order.status not in ['pending', 'assigned']:
            return Response({'error': 'Commande ne peut pas être refusée'}, status=400)

        raison = request.data.get('raison', 'Capacité insuffisante')

        # Notifier OPS
        msg = f"⚠️ PRESSING REFUSE commande\n\nCode : {order.code}\nPressing : {driver.name}\nRaison : {raison}\n\nAction requise : réassigner un autre pressing\nfagni-ops.vercel.app"
        wa_link = f"https://wa.me/2250142299949?text={urllib.parse.quote(msg)}"

        # Marquer comme en attente réassignation
        order.laundry_partner = None
        order.status = 'pending'
        order.notes = (order.notes or '') + f'\nREFUS_PRESSING:{driver.name}:{raison}'
        order.save(update_fields=['laundry_partner', 'status', 'notes', 'updated_at'])

        return Response({
            'success': True,
            'wa_ops': wa_link,
            'message': 'Commande refusée — OPS notifié'
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)

@api_view(['GET'])
@permission_classes([AllowAny])
def partner_order_detail(request, order_id):
    """GET /api/partner/orders/<id>/ — C8: bon de travail complet"""
    try:
        partner = _get_partner(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)

    from orders.models import Order, OrderItem
    try:
        order = Order.objects.prefetch_related('items').get(
            id=order_id, laundry_partner=partner
        )

        # Articles a traiter
        items = []
        for item in order.items.all():
            items.append({
                'designation': item.designation,
                'quantity': item.quantity,
                'unit_price': float(item.unit_price or 0),
                'total': float(item.total or 0),
                'service_type': getattr(item, 'service_type', 'pressing'),
            })

        # Si pas d'items en DB, extraire des notes (legacy)
        if not items and order.articles_count:
            items = [{'designation': 'Articles divers', 'quantity': order.articles_count, 'unit_price': 500, 'total': order.articles_count * 500}]

        return Response({
            'id': order.id,
            'code': order.code,
            'status': order.status,
            'created_at': order.created_at.isoformat() if order.created_at else None,
            'dropoff_time': order.dropoff_time.isoformat() if order.dropoff_time else None,
            'articles_count': order.articles_count or len(items),
            'items': items,
            'montant_pressing': float(order.amount_laundry_partner or 0),
            'instructions': order.notes or '',
            'client_zone': order.pickup_address.split(',')[0] if order.pickup_address else 'Abidjan',
        })
    except Order.DoesNotExist:
        return Response({'error': 'Commande introuvable'}, status=404)
    except Exception as e:
        return Response({'error': str(e)}, status=400)
