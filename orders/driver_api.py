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

    # Toutes les commandes assignées à ce livreur
    pickup_orders = Order.objects.filter(
        pickup_driver=driver
    ).exclude(status='done').select_related('customer', 'laundry_partner').order_by('-created_at')[:10]

    delivery_orders = Order.objects.filter(
        delivery_partner=driver
    ).exclude(status='done').select_related('customer', 'laundry_partner').order_by('-created_at')[:10]

    pickup_list  = list(pickup_orders)
    # Éviter les doublons si même commande assignée aux deux
    delivery_ids = {o.id for o in pickup_list}
    delivery_list = [o for o in list(delivery_orders) if o.id not in delivery_ids]
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
            'partner_lat':     float(getattr(o.laundry_partner, 'lat', None)) if o.laundry_partner and getattr(o.laundry_partner, 'lat', None) else None,
            'partner_lng':     float(getattr(o.laundry_partner, 'lng', None)) if o.laundry_partner and getattr(o.laundry_partner, 'lng', None) else None,
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
            if p.startswith('+'): p = p.lstrip('+')
            elif p.startswith('0'): p = '225' + p
            else: p = '225' + p
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

        # Recalculer le total depuis les articles réels
        from orders.pricing import calculate_order_total
        from django.utils import timezone
        pricing = calculate_order_total(articles_count)

        wave_link_url = f"https://pay.wave.com/m/M_ci_8SO-R9nJg71k/c/ci/?amount={pricing['total']}"
        expires_at = timezone.now() + timezone.timedelta(hours=24)

        # Utiliser queryset.update() pour bypasser le payment guard
        from orders.models import Order as _Order
        from orders.pricing_engine import calculate_order as _calc_engine
        _pe = _calc_engine(articles_count)
        _margin = _pe['total_fagni']
        _total  = pricing['total'] or 1
        _score  = round(_margin / _total * 100, 2)
        _label  = ('rentable' if _score >= 30 else 'limite' if _score >= 15 else 'a_perte')
        _Order.objects.filter(pk=order.pk).update(
            articles_count       = articles_count,
            total_client_ttc     = pricing['total'],
            delivery_fee         = pricing['delivery_fee'],
            service_fee          = pricing['service_fee'],
            payment_status       = 'awaiting_payment',
            payment_expires_at   = expires_at,
            cost_driver_pickup   = _pe['part_livreur'] // 2,
            cost_driver_delivery = _pe['part_livreur'] - _pe['part_livreur'] // 2,
            cost_pressing        = _pe['part_pressing'],
            margin_net           = _margin,
            profitability_score  = _score,
            profitability_label  = _label,
        )

        # Event logging LOT1
        try:
            from orders.models import log_event
            log_event(
                "pickup.done", order=order,
                actor_type="driver", actor_id=driver.id,
                articles_count=articles_count,
                total=pricing['total'],
                margin_net=_margin,
                profitability_label=_label,
            )
        except Exception:
            pass
        # wave_link stocké dans notes pour compatibilité
        try:
            from orders.models import Order as _O2
            o2 = _O2.objects.get(pk=order.pk)
            notes = o2.notes or ''
            # Supprimer ancien wave_link si présent
            import re
            notes = re.sub(r'WAVE_LINK:[^\s|]*', '', notes).strip()
            notes = (notes + f' | WAVE_LINK:{wave_link_url}').strip(' |')
            _O2.objects.filter(pk=order.pk).update(notes=notes)
        except Exception:
            pass

        return Response({
            'success':        True,
            'wa_client':      wa_link,
            'message':        f'Collecte confirmée — {articles_count} articles',
            'total':          pricing['total'],
            'articles_count': articles_count,
            'payment_status': 'awaiting_payment',
            'wave_link':      wave_link_url,
        })
    except Exception as e:
        import traceback
        return Response({'error': str(e), 'traceback': traceback.format_exc()}, status=400)


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

        # Event logging LOT1
        try:
            from orders.models import log_event
            log_event(
                "delivery.done", order=order,
                actor_type="driver", actor_id=driver.id,
                driver_name=driver.name,
            )
        except Exception:
            pass

        # WhatsApp client — livraison confirmée
        client_phone = order.customer.phone if order.customer else ''
        wa_link = ''
        if client_phone:
            p = client_phone.replace(' ','').replace('-','')
            if p.startswith('+'): p = p.lstrip('+')
            elif p.startswith('0'): p = '225' + p
            else: p = '225' + p
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
        from orders.models import Order
        order = Order.objects.get(id=order_id)
        notes = order.notes or ''
        order.notes = notes + f'\nDEPOSE_PRESSING:{driver.name}'
        order.save(update_fields=['notes', 'updated_at'])
        return Response({'success': True, 'message': 'Dépôt au pressing confirmé'})
    except Exception as e:
        return Response({'error': str(e)}, status=400)
