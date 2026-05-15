"""API Livreur FAGNI"""
import jwt
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


def _get_driver(request):
    token = request.headers.get('Authorization','').replace('Bearer ','')
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    from partners.models import DeliveryPartner
    return DeliveryPartner.objects.get(id=payload['did'])


@api_view(['POST'])
@permission_classes([AllowAny])
def driver_login(request):
    """POST /api/driver/login/ — {phone}"""
    phone = (request.data.get('phone') or '').strip()
    if not phone:
    return Response({'error': 'Numéro requis'}, status=400)
    try:
        from partners.models import DeliveryPartner
        driver = DeliveryPartner.objects.filter(phone=phone, is_active=True).first()
        if not driver:
            raise Exception('not found')
    except:
    return Response({'error': 'Livreur non trouvé'}, status=404)

    token = jwt.encode(
        {'did': driver.id, 'name': driver.name},
        settings.SECRET_KEY, algorithm='HS256'
    )
    return Response({
        'access': token,
        'driver': {
            'id': driver.id,
            'name': driver.name,
            'phone': driver.phone,
            'vehicle': driver.vehicle_type or 'moto'
        }
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def driver_missions(request):
    """GET /api/driver/missions/ — missions du livreur"""
    try:
        driver = _get_driver(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order

    # Missions collecte
    pickup_orders = Order.objects.filter(
        pickup_driver=driver,
        status__in=['pending']
    ).select_related('customer', 'laundry_partner').order_by('-created_at')[:10]

    # Missions livraison
    delivery_orders = Order.objects.filter(
        delivery_partner=driver,
        status__in=['in_progress', 'done']
    ).select_related('customer', 'laundry_partner').order_by('-created_at')[:10]

    pickup_list = list(pickup_orders)
    delivery_list = list(delivery_orders)
    all_orders = pickup_list + delivery_list

    result = []
    for o in all_orders:
        is_pickup = o in pickup_list
        result.append({
            'mission_id':      o.id,
            'order_id':        o.id,
            'order_code':      o.code or str(o.id),
            'mission_type':    'pickup' if is_pickup else 'delivery',
            'status':          'assigned',
            'zone':            (o.pickup_address or '').split(',')[0] if o.pickup_address else 'Abidjan',
            'pickup_address':  o.pickup_address or '',
            'partner_name':    o.laundry_partner.name if o.laundry_partner else '',
            'partner_address': o.laundry_partner.address if o.laundry_partner else '',
            'partner_lat':     float(o.laundry_partner.lat) if o.laundry_partner and o.laundry_partner.lat else None,
            'partner_lng':     float(o.laundry_partner.lng) if o.laundry_partner and o.laundry_partner.lng else None,
            'delivery_address': o.pickup_address or '',
            'delivery_lat':    float(o.pickup_lat) if o.pickup_lat else None,
            'delivery_lng':    float(o.pickup_lng) if o.pickup_lng else None,
            'pickup_lat':      float(o.pickup_lat) if o.pickup_lat else None,
            'pickup_lng':      float(o.pickup_lng) if o.pickup_lng else None,
            'bag_size':        o.bag_size or '',
            'order_status':    o.status,
            'created_at':      o.created_at.isoformat() if o.created_at else None,
        })

    return Response({'missions': result, 'driver': driver.name})

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_confirm_pickup(request, order_id):
    """POST /api/driver/orders/<id>/pickup/ — confirmer collecte"""
    try:
        driver = _get_driver(request)
    except:
    return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order, OrderEvidencePhoto
    try:
        order = Order.objects.get(id=order_id)
        articles_count = request.data.get('articles_count', 0)
        notes = request.data.get('notes', '')

        # Mettre à jour les notes avec le compte articles
        existing_notes = order.notes or ''
        order.notes = existing_notes + f'\nCOLLECTE:{articles_count} articles. {notes}'
        order.save(update_fields=['notes', 'updated_at'])

        # Générer lien WhatsApp reçu pour le client
        client_phone = order.customer.phone if order.customer else ''
        if client_phone:
            p = client_phone.replace(' ','').replace('-','')
            if not p.startswith('+'): p = '225' + p.lstrip('0')
            bag = {'small':'Petit sac','medium':'Sac moyen','large':'Grand sac'}.get(order.bag_size or '','Sac')
            msg = (
                f"Bonjour ! Votre collecte FAGNI est confirmée.\n"
                f"Commande : {order.code}\n"
                f"Sac : {bag}\n"
                f"Articles comptés : {articles_count}\n"
                f"Vos vêtements sont entre de bonnes mains !"
            )
            encoded = msg.replace(' ','%20').replace('\n','%0A')
            wa_link = f"https://wa.me/{p}?text={encoded}"
        else:
            wa_link = ''

    return Response({
            'success': True,
            'wa_client': wa_link,
            'message': f'Collecte confirmée — {articles_count} articles'
        })
    except Exception as e:
    return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def driver_confirm_delivery(request, order_id):
    """POST /api/driver/orders/<id>/delivery/ — confirmer livraison"""
    try:
        driver = _get_driver(request)
    except:
    return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order
    try:
        order = Order.objects.get(id=order_id)
        order.status = 'done'
        notes = order.notes or ''
        order.notes = notes + f'\nLIVRAISON:Confirmée par livreur {driver.name}'
        order.save(update_fields=['status', 'notes', 'updated_at'])

        # WhatsApp client — livraison confirmée
        client_phone = order.customer.phone if order.customer else ''
        wa_link = ''
        if client_phone:
            p = client_phone.replace(' ','').replace('-','')
            if not p.startswith('+'): p = '225' + p.lstrip('0')
            msg = (
                f"Bonjour ! Vos vêtements FAGNI ont été livrés.\n"
                f"Commande : {order.code}\n"
                f"Merci de votre confiance !\n"
                f"Notez votre expérience : fagni-client.vercel.app"
            )
            encoded = msg.replace(' ','%20').replace('\n','%0A')
            wa_link = f"https://wa.me/{p}?text={encoded}"

    return Response({'success': True, 'wa_client': wa_link})
    except Exception as e:
    return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def api_driver_dropoff(request, order_id):
    """POST /api/driver/orders/<id>/dropoff/ — livreur dépose au pressing"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from partners.models import DeliveryPartner
        driver = DeliveryPartner.objects.get(id=payload['did'])
    except Exception:
    return Response({'error': 'Non autorisé'}, status=401)

    try:
        order = Order.objects.get(id=order_id)
        notes = order.notes or ''
        order.notes = notes + f'\nDEPOSE_PRESSING:{driver.name}'
        order.save(update_fields=['notes', 'updated_at'])
    return Response({'success': True, 'message': 'Dépôt au pressing confirmé'})
    except Exception as e:
    return Response({'error': str(e)}, status=400)
