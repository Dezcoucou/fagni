import random
import string

def _send_notif_ops(order, title="FAGNI OPS", body=None):
    try:
        from orders.models import FCMToken
        from fagni.notifications import send_push
        code = getattr(order, 'code', str(getattr(order, 'id', '')))
        body = body or f"Commande {code} à traiter"
        tokens = FCMToken.objects.filter(user_type='ops')
        for t in tokens:
            send_push(t.token, title, body, {"type": "ops", "order_id": getattr(order, "id", ""), "order_code": code})
    except Exception as e:
        print(f"[NOTIF] ops: {e}")


def _send_notif_mission(order, driver):
    try:
        from orders.models import FCMToken
        from fagni.notifications import notif_mission_assignee
        t = FCMToken.objects.filter(user_type='driver', user_id=driver.id).first()
        if t:
            notif_mission_assignee(t.token, getattr(order, 'code', str(order.id)))
    except Exception as e:
        print(f"[NOTIF] mission: {e}")

def _send_notif_pressing(order):
    try:
        from orders.models import FCMToken
        from fagni.notifications import notif_pressing_commande
        partner = getattr(order, 'laundry_partner', None)
        if partner:
            t = FCMToken.objects.filter(user_type='partner', user_id=partner.id).first()
            if t:
                notif_pressing_commande(t.token, getattr(order, 'code', str(order.id)))
    except Exception as e:
        print(f"[NOTIF] pressing: {e}")

def _send_notif_client_livraison(order):
    try:
        from orders.models import FCMToken
        from fagni.notifications import notif_client_livraison
        customer = getattr(order, 'customer', None)
        if customer:
            t = FCMToken.objects.filter(user_type='client', user_id=customer.id).first()
            if t:
                notif_client_livraison(t.token, getattr(order, 'code', str(order.id)))
    except Exception as e:
        print(f"[NOTIF] client livraison: {e}")
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


def _parse_adjustment_note(notes):
    """Parse la derniere ligne AJUSTEMENT: dans les notes d'une commande (ADR-023).
    Format : AJUSTEMENT:type|declare|collecte|montant|resultat
    """
    if not notes or 'AJUSTEMENT:' not in notes:
        return None
    lines = [l for l in notes.split('\n') if l.startswith('AJUSTEMENT:')]
    if not lines:
        return None
    try:
        parts = lines[-1].replace('AJUSTEMENT:', '').split('|')
        return {
            'type': parts[0],
            'declared_quantity': int(parts[1]),
            'collected_quantity': int(parts[2]),
            'amount': float(parts[3]),
            'result': parts[4] if len(parts) > 4 else '',
        }
    except Exception:
        return None


@api_view(['GET'])
@permission_classes([AllowAny])
def ops_dashboard(request):
    """GET /api/ops/dashboard/ — vue globale toutes commandes"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order, Paiement
    from partners.models import LaundryPartner
    from partners.services import get_partner_payment_delay_days, get_partner_payment_label

    # Filtres
    status = request.GET.get('status', '')
    partner_id = request.GET.get('partner', '')
    adjusted = request.GET.get('adjusted', '')

    qs = Order.objects.select_related('customer', 'laundry_partner').order_by('-created_at')
    if status:
        qs = qs.filter(status=status)
    if partner_id:
        qs = qs.filter(laundry_partner_id=partner_id)
    if adjusted == 'true':
        qs = qs.filter(notes__contains='AJUSTEMENT:')

    orders = []
    for o in qs[:100]:
        phone = o.customer.phone if o.customer else ''
        partner = o.laundry_partner

        # Générer lien WhatsApp client
        client_phone = phone.replace(' ','').replace('-','')
        if client_phone and not client_phone.startswith('+'):
            client_phone = '225' + client_phone if client_phone.startswith('0') else '225' + client_phone.lstrip('+')

            wa_client = ''
            if client_phone:
                # Message dynamique selon le vrai statut (9 juillet 2026, Pilot Growth Plan)
                _wa_status_messages = {
                    'pending': f"Bonjour ! Nous avons bien reçu votre commande FAGNI {o.code}. Elle est en cours de traitement, nous vous tiendrons informé(e).",
                    'assigned': f"Bonjour ! Votre commande FAGNI {o.code} a été assignée à un livreur, il arrive bientôt pour la collecte.",
                    'in_progress': f"Bonjour ! Votre commande FAGNI {o.code} est en cours de collecte.",
                    'at_pressing': f"Bonjour ! Votre commande FAGNI {o.code} est chez notre partenaire pressing.",
                    'ready': f"Bonjour ! Votre commande FAGNI {o.code} est prête. Le livreur arrive bientôt pour la livraison.",
                    'delivering': f"Bonjour ! Votre commande FAGNI {o.code} est en cours de livraison.",
                    'done': f"Bonjour ! Votre commande FAGNI {o.code} a été livrée. Merci de votre confiance !",
                }
                msg = _wa_status_messages.get(o.status, f"Bonjour ! Votre commande FAGNI {o.code} est en cours de traitement.")
                wa_client = f"https://wa.me/{client_phone}?text={msg.replace(' ','%20')}"

        # Générer lien WhatsApp partenaire
        wa_partner = ''
        if partner and partner.phone:
            p_phone = partner.phone.replace(' ','').replace('-','')
            if not p_phone.startswith('+'):
                p_phone = '225' + p_phone if p_phone.startswith('0') else '225' + p_phone.lstrip('+')
            bag = {'small':'Petit sac','medium':'Sac moyen','large':'Grand sac'}.get(o.bag_size or '','Sac')
            msg2 = f"Bonjour {partner.name} ! Nouvelle commande FAGNI {o.code} - {bag}. Le livreur arrive."
            wa_partner = f"https://wa.me/{p_phone}?text={msg2.replace(' ','%20')}"

        orders.append({
            'id':             o.id,
            'code':           o.code or str(o.id),
            'status':         o.status,
            'payment_status': o.payment_status,
            'lavage_termine': o.wash_complete_time.isoformat() if o.wash_complete_time else None,
            'service_type':   o.service_type or 'pressing',
            'total': float(max(0, (o.total_client_ttc or o.total or 0) - (o.coupon_discount_applied or 0))),
            'bag_size':       o.bag_size or '',
            'customer_name':  o.customer.name if o.customer else '—',
            'customer_phone': o.customer.phone if o.customer else '—',
            'partner_name':   partner.name if partner else None,
            'partner_id':     partner.id if partner else None,
            'wa_client':      wa_client,
            'wa_partner':     wa_partner,
            'is_premium':     getattr(o, 'notes', '') and 'VALEUR:premium' in (o.notes or ''),
            'valeur_declaree': 'premium' if 'VALEUR:premium' in (o.notes or '') else 'sensible' if 'VALEUR:sensible' in (o.notes or '') else 'standard',
            'delivery_driver_name': o.delivery_partner.name if o.delivery_partner else None,
            'delivery_driver_id':   o.delivery_partner.id if o.delivery_partner else None,
            'pickup_driver_name':   o.pickup_driver.name if o.pickup_driver else None,
            'pickup_driver_id':     o.pickup_driver.id if o.pickup_driver else None,
            'created_at':        o.created_at.isoformat() if o.created_at else None,
            'margin_net': float((o.margin_net or 0) - (o.coupon_discount_applied or 0)),
            'profitability_score': (
                round((float((o.margin_net or 0) - (o.coupon_discount_applied or 0)) / float(max(1, (o.total_client_ttc or 0) - (o.coupon_discount_applied or 0)))) * 100, 1)
                if o.coupon_discount_applied else float(o.profitability_score or 0)
            ),
            'profitability_label': o.profitability_label or '',
            'cost_driver_pickup':  o.cost_driver_pickup or 0,
            'cost_driver_delivery': o.cost_driver_delivery or 0,
            'cost_pressing':       o.cost_pressing or 0,
            'notes': o.notes or '',
            'has_adjustment': 'AJUSTEMENT:' in (o.notes or ''),
            'adjustment_info': _parse_adjustment_note(o.notes),
            'items': [{'designation': it.designation, 'quantity': it.quantity, 'unit_price': float(it.unit_price or 0), 'total': float(it.total or 0)} for it in o.items.all()],
        })

    # Stats globales
    from django.db.models import Sum, Avg, Count
    all_orders = Order.objects.all()
    done_orders = all_orders.filter(status='done')

    marge_totale = done_orders.aggregate(t=Sum('margin_net'))['t'] or 0
    marge_moy    = done_orders.aggregate(t=Avg('margin_net'))['t'] or 0
    score_moy    = done_orders.aggregate(t=Avg('profitability_score'))['t'] or 0

    from orders.models import FagniEvent
    from django.utils import timezone
    today = timezone.now().date()

    stats = {
        'total':            all_orders.count(),
        'pending':          all_orders.filter(status='pending').count(),
        'in_progress':      all_orders.filter(status='in_progress').count(),
        'done':             done_orders.count(),
        'unpaid':           all_orders.filter(payment_status='unpaid').count(),
        'revenue':          float(all_orders.filter(payment_status='paid').aggregate(
                                t=Sum('total'))['t'] or 0),
        'marge_totale':     int(marge_totale),
        'marge_moyenne':    int(marge_moy),
        'score_moyen':      round(float(score_moy), 1),
        'rentables':        all_orders.filter(profitability_label='rentable').count(),
        'limites':          all_orders.filter(profitability_label='limite').count(),
        'a_perte':          all_orders.filter(profitability_label='a_perte').count(),
        'events_today':     FagniEvent.objects.filter(
                                created_at__date=today).count(),
        'events_total':     FagniEvent.objects.count(),
    }

    # Partenaires pour filtre
    partners_qs = (
        LaundryPartner.objects
        .filter(is_active=True)
        .order_by('-partner_score', 'name')
    )

    partners = []

    for ptn in partners_qs:
        partners.append({
            'id': ptn.id,
            'name': ptn.name,
            'phone': ptn.phone,
            'wave_number': ptn.wave_number,
            'partner_type': ptn.partner_type,
            'partner_score': ptn.partner_score,
            'level': ptn.level,
            'score_delai': ptn.score_delai,
            'score_litiges': ptn.score_litiges,
            'score_dispo': ptn.score_dispo,
            'score_refus': ptn.score_refus,
            'payment_delay_days': get_partner_payment_delay_days(ptn),
            'payment_label': get_partner_payment_label(ptn),
        })

    # Livreurs
    from partners.models import DeliveryPartner
    drivers = list(DeliveryPartner.objects.filter(is_active=True).values('id','name','phone','wave_number','is_online'))

    return Response({'orders': orders, 'stats': stats, 'partners': partners, 'drivers': drivers})



def _suggest_best_pressing(order):
    """
    Calcule et trie les pressings actifs par score composite :
    - Distance Haversine client → pressing (60%)
    - Partner score /100 (40%)
    Retourne une liste de dicts avec pressing, distance_km, delivery_fee, score_composite.
    """
    from partners.models import LaundryPartner
    from orders.utils.distances import osrm_distance_km
    from orders.config_models import GlobalPricingSettings
    from decimal import Decimal

    cfg = GlobalPricingSettings.get_solo()
    price_per_km = Decimal(str(cfg.delivery_price_per_km or 150))
    min_fee = Decimal(str(cfg.delivery_min_fee or 2000))

    client_lat = getattr(order, 'pickup_lat', None) or getattr(order, 'delivery_lat', None)
    client_lng = getattr(order, 'pickup_lng', None) or getattr(order, 'delivery_lng', None)

    pressings = LaundryPartner.objects.filter(is_active=True)
    results = []

    for p in pressings:
        if not p.latitude or not p.longitude:
            continue
        dist = osrm_distance_km(client_lat, client_lng, float(p.latitude), float(p.longitude))
        if dist is None:
            continue
        # delivery_fee = aller-retour avec plancher
        fee = max(min_fee, dist * 2 * price_per_km)
        fee = int(fee.quantize(Decimal('1')))
        # Score composite : 60% distance (inversé), 40% partner_score
        # Distance normalisée sur 10 km max
        dist_score = max(0, 1 - float(dist) / 10) * 60
        partner_score = float(p.partner_score or 0) / 100 * 40
        composite = round(dist_score + partner_score, 2)
        results.append({
            'id': p.id,
            'name': p.name,
            'phone': p.phone or '',
            'address': p.address or '',
            'distance_km': float(dist),
            'delivery_fee': fee,
            'partner_score': p.partner_score or 0,
            'level': p.level or 'bronze',
            'composite_score': composite,
        })

    results.sort(key=lambda x: x['composite_score'], reverse=True)
    return results[:3]

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
        order.laundry_partner_id = partner.id if partner else None

        # Recalcul delivery_fee au km réel après assignation pressing
        if partner and getattr(partner, 'latitude', None) and getattr(partner, 'longitude', None):
            from orders.utils.distances import osrm_distance_km
            from orders.config_models import GlobalPricingSettings
            from decimal import Decimal as _D
            _cfg = GlobalPricingSettings.get_solo()
            _price_km = _D(str(_cfg.delivery_price_per_km or 150))
            _min_fee = _D(str(_cfg.delivery_min_fee or 2000))
            client_lat = order.pickup_lat or order.delivery_lat
            client_lng = order.pickup_lng or order.delivery_lng
            dist = osrm_distance_km(
                client_lat, client_lng,
                float(partner.latitude), float(partner.longitude)
            )
            if dist is not None:
                fee = max(_min_fee, dist * 2 * _price_km)
                fee_int = int(fee.quantize(_D('1')))
                order.delivery_fee = fee_int
                order.distance_km_pickup = dist
                order.distance_km_delivery = dist
                order.distance_km_total = dist * 2
                order.distance_km = dist * 2
                # Recalcul total client avec nouveau delivery_fee
                try:
                    from orders.pricing_engine import calculate_order
                    from django.db.models import Sum
                    nb = order.items.aggregate(s=Sum('quantity'))['s'] or order.articles_count or 1
                    pricing = calculate_order(nb, order.bag_size or 'small', delivery_fee=fee_int)
                    order.total_client_ttc = pricing['total_client_ttc']
                    order.total = pricing['total_client']
                    order.service_fee = pricing['service_fee']
                    order.amount_laundry_partner = pricing['amount_laundry_partner']
                    order.fagni_revenue_ht = pricing['fagni_revenue_ht']
                    order.margin_net = pricing['total_fagni']
                except Exception:
                    import logging
                    logging.getLogger("fagni.ops_api").exception("Echec silencieux: recalcul pricing apres changement partenaire/distance | order_id=%s", getattr(order, "id", None))

        order.save(update_fields=[
            'laundry_partner_id', 'delivery_fee',
            'distance_km_pickup', 'distance_km_delivery',
            'distance_km_total', 'distance_km',
            'total_client_ttc', 'total', 'service_fee',
            'amount_laundry_partner', 'fagni_revenue_ht', 'margin_net'
        ])
        if partner:
            _send_notif_pressing(order)
            # Notifier le client que son prix est confirmé
            try:
                from orders.models import FCMToken
                from fagni.notifications import send_push
                customer = getattr(order, 'customer', None)
                if customer:
                    t = FCMToken.objects.filter(user_type='client', user_id=customer.id).first()
                    if t:
                        total = int(max(0, (getattr(order, 'total_client_ttc', 0) or 0) - (getattr(order, 'coupon_discount_applied', 0) or 0)))
                        send_push(
                            t.token,
                            "Prix confirmé ✅",
                            f"Votre commande {order.code} est confirmée — Total : {total:,} FCFA",
                            {"type": "price_confirmed", "order_id": str(order.id), "total": str(total)}
                        )
            except Exception:
                import logging
                logging.getLogger("fagni.orders.ops_api").exception("Exception silencieuse (auto-log) - fichier=orders/ops_api.py ligne=355")
            # Notification OPS supprimée ici : OPS vient déjà de faire l’action.
            try:
                from orders.models import log_event
                log_event(
                    "partner.assigned",
                    order=order,
                    actor_type="ops",
                    actor_id=None,
                    partner_id=partner.id,
                    partner_name=partner.name,
                )
            except Exception:
                import logging
                logging.getLogger("fagni.ops_api").exception("Echec silencieux: partner.assigned (log_event) | order_id=%s", getattr(order, "id", None))
        return Response({'success': True, 'partner': partner.name if partner else None})
    except Exception as e:
        return Response({'error': str(e)}, status=400)






def _suggest_best_driver(order, leg_type="pickup"):
    """
    Suggere les 3 meilleurs livreurs disponibles par Proximi-Score :
    - 50% distance routiere OSRM livreur -> point de collecte
    - 30% driver_score
    - 20% disponibilite (missions actives)
    leg_type='pickup'  : point = adresse client
    leg_type='return'  : point = adresse pressing
    """
    from partners.models import DeliveryPartner
    from orders.utils.distances import osrm_distance_km
    from orders.config_models import GlobalPricingSettings
    from orders.models import DeliveryLeg
    from decimal import Decimal

    cfg = GlobalPricingSettings.get_solo()
    price_per_km = Decimal(str(cfg.delivery_price_per_km or 200))
    min_fee = Decimal(str(cfg.delivery_min_fee or 2000))
    driver_share = Decimal(str(cfg.driver_share_ratio or 70)) / 100
    min_leg = Decimal(str(cfg.driver_amount_per_leg or 800))

    # Point de reference selon type de jambe
    if leg_type == "pickup":
        ref_lat = getattr(order, 'pickup_lat', None) or getattr(order, 'delivery_lat', None)
        ref_lng = getattr(order, 'pickup_lng', None) or getattr(order, 'delivery_lng', None)
    else:
        pressing = getattr(order, 'laundry_partner', None)
        ref_lat = float(pressing.latitude) if pressing and pressing.latitude else None
        ref_lng = float(pressing.longitude) if pressing and pressing.longitude else None

    if not ref_lat or not ref_lng:
        return []

    results = []
    for d in DeliveryPartner.objects.filter(is_active=True, is_online=True):
        if not d.latitude or not d.longitude:
            continue
        dist = osrm_distance_km(float(d.latitude), float(d.longitude), ref_lat, ref_lng)
        if dist is None:
            continue

        # Missions actives
        missions_actives = DeliveryLeg.objects.filter(
            driver=d, status__in=["assigned", "in_progress"]
        ).count()
        if missions_actives >= 3:
            continue  # Livreur surchargé

        # Remunération dynamique par jambe
        remun_leg = max(min_leg, dist * driver_share * price_per_km)
        remun_leg = int(remun_leg.quantize(Decimal("1")))

        # Score composite
        dist_score = max(0, 1 - float(dist) / 5) * 50
        driver_score_w = float(getattr(d, 'driver_score', 80) or 80) / 100 * 30
        dispo_score = (1 - missions_actives / 3) * 20
        composite = round(dist_score + driver_score_w + dispo_score, 2)

        results.append({
            'id': d.id,
            'name': d.name,
            'phone': d.phone or '',
            'vehicle': d.vehicle_type or 'moto',
            'distance_km': float(dist),
            'remun_leg': remun_leg,
            'missions_actives': missions_actives,
            'is_online': d.is_online,
            'composite_score': composite,
        })

    results.sort(key=lambda x: x['composite_score'], reverse=True)
    return results[:3]

@api_view(['GET'])
@permission_classes([AllowAny])
def ops_suggest_pressing(request, order_id):
    """GET /api/ops/orders/<id>/suggest-pressing/ — top 3 pressings suggérés"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)
    from orders.models import Order
    try:
        order = Order.objects.get(id=order_id)
        suggestions = _suggest_best_pressing(order)
        if not suggestions:
            return Response({'error': 'Aucun pressing actif avec GPS disponible'}, status=404)
        return Response({
            'order_id': order_id,
            'order_code': order.code,
            'client_lat': order.pickup_lat,
            'client_lng': order.pickup_lng,
            'suggestions': suggestions,
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)



@api_view(['GET'])
@permission_classes([AllowAny])
def ops_suggest_driver(request, order_id):
    """GET /api/ops/orders/<id>/suggest-driver/?leg=pickup|return"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorise'}, status=401)
    from orders.models import Order
    try:
        order = Order.objects.get(id=order_id)
        leg_type = request.GET.get('leg', 'pickup')
        suggestions = _suggest_best_driver(order, leg_type=leg_type)
        if not suggestions:
            return Response({'error': 'Aucun livreur disponible avec GPS'}, status=404)
        return Response({
            'order_id': order_id,
            'order_code': order.code,
            'leg_type': leg_type,
            'suggestions': suggestions,
        })
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
        # Recalcul Partner Score si statut terminal
        if new_status in ('done', 'canceled') and order.laundry_partner_id:
            try:
                from partners.services import recompute_partner_score
                recompute_partner_score(order.laundry_partner)
            except Exception:
                pass  # Score non bloquant

        return Response({'success': True, 'status': new_status})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def ops_update_litige_status(request, order_id):
    """POST /api/ops/orders/<id>/litige-status/ — {status, resolution_note}"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order
    ALLOWED_LITIGE = ['ouvert', 'en_enquete', 'en_attente_client', 'en_attente_partner', 'resolu', 'refuse', 'rembourse']
    try:
        order = Order.objects.get(id=order_id)
        new_status = request.data.get('status', '')
        resolution_note = request.data.get('resolution_note', '')
        if new_status not in ALLOWED_LITIGE:
            return Response({'error': 'Statut litige invalide'}, status=400)

        notes = order.notes or ''
        lines_notes = notes.split('\n')
        new_lines = []
        found = False
        for line in lines_notes:
            if line.startswith('LITIGE:'):
                found = True
                parts = line.replace('LITIGE:', '').split('|')
                litige_type = parts[0] if len(parts) > 0 else ''
                litige_desc = parts[1] if len(parts) > 1 else ''
                new_lines.append(f'LITIGE:{litige_type}|{litige_desc}|{new_status}|{resolution_note}')
            else:
                new_lines.append(line)
        if not found:
            return Response({'error': 'Aucun litige trouve sur cette commande'}, status=400)

        order.notes = '\n'.join(new_lines)
        order.save(update_fields=['notes', 'updated_at'])

        return Response({'success': True, 'status': new_status})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def ops_assign_driver(request, order_id):
    """POST /api/ops/orders/<id>/assign-driver/ — {driver_id}"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order
    from partners.models import DeliveryPartner
    try:
        order = Order.objects.get(id=order_id)
        driver_id = request.data.get('driver_id')
        driver = DeliveryPartner.objects.get(id=driver_id) if driver_id else None
        driver_type = request.data.get('driver_type', 'pickup')
        from decimal import Decimal
        from orders.models import DeliveryLeg, sync_delivery_legs_for_order

        # Verrou pilote : cet endpoint est reserve a la collecte.
        # Le retour officiel passe par /assign-return-driver/ apres pressing PRET.
        if driver_type != 'pickup':
            return Response({
                'error': 'Endpoint reserve a la collecte. Utiliser assign-return-driver pour le retour.'
            }, status=400)

        from orders.config_models import GlobalPricingSettings as _GPS1
        _driver_amount_pickup = Decimal(str(_GPS1.get_solo().driver_amount_per_leg))
        order.pickup_driver = driver
        order.cost_driver_pickup = int(_driver_amount_pickup)
        order.save(update_fields=['pickup_driver', 'cost_driver_pickup', 'updated_at'])

        leg, _ = DeliveryLeg.objects.get_or_create(
            order=order,
            leg_type='pickup',
            defaults={
                'driver': driver,
                'status': 'pending',
                'driver_amount': _driver_amount_pickup,
            }
        )
        assigned_now = False
        from orders.config_models import GlobalPricingSettings as _GPS3
        _dap = Decimal(str(_GPS3.get_solo().driver_amount_per_leg))
        leg.driver = driver
        if leg.status == 'pending':
            leg.status = 'assigned'
            assigned_now = True
        leg.driver_amount = _dap
        leg.save(update_fields=['driver', 'status', 'driver_amount'])

        event_type = 'pickup.assigned'
        if assigned_now:
            _send_notif_mission(order, driver)

        # Event logging LOT1
        try:
            from orders.models import log_event
            log_event(
                event_type, order=order,
                actor_type="ops", actor_id=None,
                driver_id=driver.id if driver else None,
                driver_name=driver.name if driver else None,
                driver_type=driver_type,
            )
        except Exception:
            import logging
            logging.getLogger("fagni.ops_api").exception("Echec silencieux: driver.assigned (log_event) | order_id=%s", getattr(order, "id", None))

        # FGP — DESACTIVE PILOTE (activation apres 5 pressings + 200 commandes)
        # FGP — Cotisation Fonds Garantie Partenaire (version legere) — DESACTIVE
        try:
            from wallets.models import Wallet, WalletTransaction as WT
            from decimal import Decimal
            from django.db import transaction as _tx
            pressing = None  # FGP DESACTIVE PILOTE
            if pressing and order.amount_laundry_partner:
                ikey_fgp = f"fgp:cotisation:{order.id}"
                if not WT.objects.filter(idempotency_key=ikey_fgp).exists():
                    cotisation = Decimal("50")
                    with _tx.atomic():
                        w, _ = Wallet.objects.select_for_update().get_or_create(
                            owner_type="laundry", laundry_partner=pressing,
                            currency="XOF", defaults={})
                        if w.balance >= cotisation:
                            w.balance = (w.balance - cotisation).quantize(Decimal("0.01"))
                            w.save(update_fields=["balance", "updated_at"])
                            WT.objects.create(
                                wallet=w, order=order, leg=None,
                                type="debit", direction="out", amount=cotisation,
                                description=f"[FGP_COTISATION] commande {order.code} | 50F cotisation + 25F abondement FAGNI",
                                idempotency_key=ikey_fgp, allow_orphan=True)
                        log_event("fgp.cotisation", order=order,
                            actor_type="system", actor_id="mark_paid",
                            data={"pressing_id": pressing.id, "cotisation": 50, "abondement_fagni": 25})
        except Exception as _e:
            import logging; logging.getLogger("fagni.fgp").warning(f"FGP failed: {_e}")

        return Response({'success': True, 'driver': driver.name if driver else None, 'type': driver_type})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def ops_mark_paid(request, order_id):
    """POST /api/ops/orders/<id>/mark-paid/ — C6: marquer commande payee + declencher payout"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorise'}, status=401)
    from orders.models import Order
    try:
        order = Order.objects.get(id=order_id)
        if order.status not in ("done"):
            return Response({"error": "Paiement impossible — commande non livrée", "status": order.status}, status=400)
        channel = request.data.get('channel', 'wave')
        reference = (request.data.get('reference') or '').strip()

        # C6 — mark_paid declenche SYSTEME 1 via signals (payout legs)
        try:
            order.mark_paid(method=channel, reference=reference or None)
        except Exception:
            order.payment_status = 'paid'
            order.amount_paid = order.total_client_ttc or order.total
            order.payment_declared_channel = channel
            order.payment_declared_reference = reference
            order.save(update_fields=[
                'payment_status', 'amount_paid',
                'payment_declared_channel', 'payment_declared_reference', 'updated_at'
            ])

        try:
            from orders.models import log_event
            log_event("payment.paid", order=order, actor_type="ops",
                actor_id=None, channel=channel, reference=reference)
        except Exception:
            import logging
            logging.getLogger("fagni.ops_api").exception("Echec silencieux: payment.paid (log_event) | order_id=%s", getattr(order, "id", None))

        return Response({
            'success': True,
            'payment_status': 'paid',
            'amount': float(order.total_client_ttc or order.total or 0),
            'channel': channel,
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)



@api_view(['POST'])
@permission_classes([AllowAny])
def ops_add_partner(request):
    """POST /api/ops/partners/add/ — {name, phone, city, address}"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from partners.models import LaundryPartner
        import cloudinary.uploader as cu
        def upload_p(b64, folder, pid):
            if not b64: return ''
            try: return cu.upload(b64, folder=folder, public_id=pid)['secure_url']
            except: return ''

        partner = LaundryPartner.objects.create(
            name=request.data.get('name','').strip(),
            phone=request.data.get('phone','').strip(),
            wave_number=request.data.get('wave_number','').strip(),
            city=request.data.get('city','Abidjan').strip(),
            address=request.data.get('address','').strip(),
            partner_type=request.data.get('partner_type','blanchisserie').strip(),
            latitude=request.data.get('lat') or None,
            longitude=request.data.get('lng') or None,
            rccm=request.data.get('rccm','').strip(),
            is_active=True
        )
        partner.photo_facade_url = upload_p(request.data.get('photo_facade',''), 'partners/facade', f'partner_{partner.id}_facade')
        partner.save(update_fields=['photo_facade_url','rccm'])
        return Response({'success': True, 'id': partner.id, 'name': partner.name})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def ops_add_driver(request):
    """POST /api/ops/drivers/add/ — {name, phone, vehicle_type}"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from partners.models import DeliveryPartner
        import cloudinary.uploader as cu
        def upload_photo(b64, folder, pid):
            if not b64: return ''
            try: return cu.upload(b64, folder=folder, public_id=pid)['secure_url']
            except: return ''

        driver = DeliveryPartner.objects.create(
            name=request.data.get('name','').strip(),
            phone=request.data.get('phone','').strip(),
            wave_number=request.data.get('wave_number','').strip(),
            vehicle_type=request.data.get('vehicle_type','moto').strip(),
            city=request.data.get('city','Abidjan').strip(),
            is_active=True,
            remuneration_collecte=int(request.data.get('remuneration_collecte', 800) or 800),
            remuneration_livraison=int(request.data.get('remuneration_livraison', 800) or 800),
            cni_number=request.data.get('cni_number','').strip(),
        )
        driver.photo_cni_url = upload_photo(request.data.get('photo_cni',''), 'drivers/cni', f'driver_{driver.id}_cni')
        driver.photo_profil_url = upload_photo(request.data.get('photo_profil',''), 'drivers/profil', f'driver_{driver.id}_profil')
        driver.save(update_fields=['photo_cni_url','photo_profil_url','cni_number'])
        return Response({'success': True, 'id': driver.id, 'name': driver.name})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([AllowAny])
def api_ops_all_photos(request):
    """GET /api/ops/photos/ — vue globale de toutes les preuves photo (OrderEvidencePhoto), toutes commandes confondues.
    Filtrable par ?kind=pickup_items|delivery_to_client|partner_before|partner_after
    Filtrable par ?order_id=<id>
    """
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)

    from orders.models import OrderEvidencePhoto

    qs = OrderEvidencePhoto.objects.select_related('order').order_by('-created_at')

    kind = request.GET.get('kind')
    if kind:
        qs = qs.filter(kind=kind)

    order_id = request.GET.get('order_id')
    if order_id:
        qs = qs.filter(order_id=order_id)

    limit = int(request.GET.get('limit', 100))
    qs = qs[:limit]

    results = []
    for photo in qs:
        results.append({
            'id': photo.id,
            'order_id': photo.order_id,
            'order_code': getattr(photo.order, 'code', ''),
            'leg_id': photo.leg_id,
            'actor_type': photo.actor_type,
            'actor_id': photo.actor_id,
            'kind': photo.kind,
            'image_url': photo.image.url if photo.image else None,
            'caption': photo.caption or '',
            'created_at': photo.created_at.isoformat() if photo.created_at else None,
        })

    total_count = OrderEvidencePhoto.objects.count()

    return Response({
        'photos': results,
        'total_count': total_count,
        'returned_count': len(results),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def api_ops_credit_client_wallet(request):
    """POST /api/ops/wallet/credit-client/ — crediter manuellement le wallet d'un client
    (recharge demandee par WhatsApp/Wave, traitee par OPS).
    Params: phone, amount, reference (optionnel), note (optionnel)
    """
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)

    from orders.models import Customer
    from wallets.services import get_or_create_wallet_for_customer, credit_wallet
    from decimal import Decimal, InvalidOperation
    from django.utils import timezone

    phone = (request.data.get('phone') or '').strip()
    reference = (request.data.get('reference') or '').strip()
    note = (request.data.get('note') or '').strip()

    try:
        amount = Decimal(str(request.data.get('amount', 0)))
    except (InvalidOperation, TypeError):
        amount = Decimal('0')

    if not phone:
        return Response({'error': 'Numero de telephone requis'}, status=400)
    if amount <= 0:
        return Response({'error': 'Montant invalide'}, status=400)

    try:
        customer = Customer.objects.filter(phone=phone).first()
        if not customer:
            return Response({'error': 'Client introuvable pour ce numero'}, status=404)

        wallet = get_or_create_wallet_for_customer(customer)

        idempotency_key = f"manual_recharge_{customer.id}_{reference}" if reference else f"manual_recharge_{customer.id}_{int(timezone.now().timestamp())}"

        description = f"Recharge manuelle OPS"
        if reference:
            description += f" — reference {reference}"
        if note:
            description += f" — {note}"

        tx = credit_wallet(
            wallet, amount,
            description=description,
            tx_type='manual_recharge',
            idempotency_key=idempotency_key,
            allow_orphan=True,
        )

        wallet.refresh_from_db()

        return Response({
            'success': True,
            'customer_name': customer.name,
            'new_balance': float(wallet.balance),
            'transaction_id': tx.id if tx else None,
        })
    except Exception as e:
        import logging
        logging.getLogger("fagni.ops_api").exception("Echec credit client wallet")
        return Response({'error': str(e)}, status=400)


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def api_ops_pilotbook(request):
    """
    GET  /api/ops/pilotbook/ - liste toutes les entrees du Pilot Book
    POST /api/ops/pilotbook/ - cree une nouvelle entree
    (FAGNI Pilot Book, 14 juillet 2026)
    """
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)

    from orders.models import PilotBookEntry, Order

    if request.method == 'GET':
        entries = PilotBookEntry.objects.select_related('order').all()
        return Response({
            'entries': [
                {
                    'id': e.id,
                    'order_id': e.order_id,
                    'order_code': e.order.code if e.order else (e.order_code_libre or None),
                    'ce_qui_a_bien_fonctionne': e.ce_qui_a_bien_fonctionne,
                    'ce_qui_a_echoue': e.ce_qui_a_echoue,
                    'cause_reelle': e.cause_reelle,
                    'decision_prise': e.decision_prise,
                    'modification_apportee': e.modification_apportee,
                    'impact_mesure': e.impact_mesure,
                    'created_at': e.created_at.isoformat() if e.created_at else None,
                }
                for e in entries
            ]
        })

    order_code_input = (request.data.get('order_code') or '').strip()
    order_obj = None
    order_code_libre = ''
    if order_code_input:
        order_obj = Order.objects.filter(code=order_code_input).first()
        if not order_obj:
            order_code_libre = order_code_input

    entry = PilotBookEntry.objects.create(
        order=order_obj,
        order_code_libre=order_code_libre,
        ce_qui_a_bien_fonctionne=(request.data.get('ce_qui_a_bien_fonctionne') or '').strip(),
        ce_qui_a_echoue=(request.data.get('ce_qui_a_echoue') or '').strip(),
        cause_reelle=(request.data.get('cause_reelle') or '').strip(),
        decision_prise=(request.data.get('decision_prise') or '').strip(),
        modification_apportee=(request.data.get('modification_apportee') or '').strip(),
        impact_mesure=(request.data.get('impact_mesure') or '').strip(),
    )

    return Response({
        'success': True,
        'entry': {
            'id': entry.id,
            'order_code': entry.order.code if entry.order else (entry.order_code_libre or None),
            'created_at': entry.created_at.isoformat(),
        }
    })


@api_view(['DELETE'])
@permission_classes([AllowAny])
def api_ops_pilotbook_detail(request, entry_id):
    """
    DELETE /api/ops/pilotbook/<id>/ - supprime une entree
    (FAGNI Pilot Book, 14 juillet 2026)
    """
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)

    from orders.models import PilotBookEntry

    try:
        entry = PilotBookEntry.objects.get(id=entry_id)
    except PilotBookEntry.DoesNotExist:
        return Response({'error': 'Entree introuvable'}, status=404)

    entry.delete()
    return Response({'success': True})


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def api_ops_prospects(request):
    """
    GET  /api/ops/prospects/ — liste tous les prospects
    POST /api/ops/prospects/ — cree un nouveau prospect
    (Pilot Growth Plan, 10 juillet 2026)
    """
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)

    from orders.models import Prospect

    if request.method == 'GET':
        prospects = Prospect.objects.all()
        return Response({
            'prospects': [
                {
                    'id': p.id,
                    'nom': p.nom,
                    'telephone': p.telephone,
                    'canal': p.canal,
                    'canal_label': p.get_canal_display(),
                    'statut': p.statut,
                    'statut_label': p.get_statut_display(),
                    'notes': p.notes,
                    'created_at': p.created_at.isoformat() if p.created_at else None,
                }
                for p in prospects
            ]
        })

    # POST - creation
    nom = (request.data.get('nom') or '').strip()
    telephone = (request.data.get('telephone') or '').strip()
    canal = (request.data.get('canal') or 'whatsapp').strip()
    notes = (request.data.get('notes') or '').strip()

    if not nom or not telephone:
        return Response({'error': 'Nom et telephone requis'}, status=400)

    valid_canaux = [c[0] for c in Prospect.CANAL_CHOICES]
    if canal not in valid_canaux:
        canal = 'autre'

    prospect = Prospect.objects.create(nom=nom, telephone=telephone, canal=canal, notes=notes)

    return Response({
        'success': True,
        'prospect': {
            'id': prospect.id,
            'nom': prospect.nom,
            'telephone': prospect.telephone,
            'canal': prospect.canal,
            'canal_label': prospect.get_canal_display(),
            'statut': prospect.statut,
            'statut_label': prospect.get_statut_display(),
            'notes': prospect.notes,
            'created_at': prospect.created_at.isoformat(),
        }
    })


@api_view(['PATCH', 'DELETE'])
@permission_classes([AllowAny])
def api_ops_prospect_detail(request, prospect_id):
    """
    PATCH  /api/ops/prospects/<id>/ — modifie le statut d'un prospect
    DELETE /api/ops/prospects/<id>/ — supprime un prospect
    (Pilot Growth Plan, 10 juillet 2026)
    """
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)

    from orders.models import Prospect

    try:
        prospect = Prospect.objects.get(id=prospect_id)
    except Prospect.DoesNotExist:
        return Response({'error': 'Prospect introuvable'}, status=404)

    if request.method == 'DELETE':
        prospect.delete()
        return Response({'success': True})

    # PATCH - modification du statut
    statut = (request.data.get('statut') or '').strip()
    valid_statuts = [s[0] for s in Prospect.STATUT_CHOICES]
    if statut not in valid_statuts:
        return Response({'error': 'Statut invalide'}, status=400)

    prospect.statut = statut
    prospect.save(update_fields=['statut', 'updated_at'])

    return Response({'success': True, 'statut': prospect.statut, 'statut_label': prospect.get_statut_display()})


@api_view(['GET'])
@permission_classes([AllowAny])
def ops_list_partners(request):
    """GET /api/ops/partners/ — liste blanchisseries + livreurs"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from partners.models import LaundryPartner, DeliveryPartner
    partners = list(LaundryPartner.objects.filter(is_active=True).values(
        'id','name','phone','city','address','wave_number',
        'partner_type','partner_score','level',
        'score_delai','score_litiges','score_dispo','score_refus'
    ))
    drivers = list(DeliveryPartner.objects.filter(is_active=True).values(
        'id','name','phone','city','vehicle_type','is_online','wave_number'
    ))
    return Response({'partners': partners, 'drivers': drivers})


@api_view(['GET'])
@permission_classes([AllowAny])
def api_ops_paiements(request):
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)
    """GET /api/ops/paiements/ — cumul gains pressings et livreurs"""
    from partners.models import LaundryPartner, DeliveryPartner
    from orders.models import Order, Paiement
    from django.db.models import Sum, Count

    # Pressings
    pressings_data = []
    for p in LaundryPartner.objects.filter(is_active=True):
        commandes = Order.objects.filter(
            laundry_partner=p,
            status='done'
        )
        nb = commandes.count()
        total_client = commandes.aggregate(s=Sum('total_client_ttc'))['s'] or 0
        a_payer = commandes.aggregate(s=Sum('amount_laundry_partner'))['s'] or 0
        pressings_data.append({
            'id': p.id,
            'name': p.name,
            'phone': p.phone or '',
            'wave_number': getattr(p, 'wave_number', '') or '',
            'nb_commandes': nb,
            'total_client': int(total_client),
            'a_payer': int(a_payer),
            'deja_paye': int(Paiement.objects.filter(partenaire_id=p.id, partenaire_type='pressing').aggregate(s=Sum('montant'))['s'] or 0),
        })

    # Livreurs
    livreurs_data = []
    for d in DeliveryPartner.objects.filter(is_active=True):
        missions_collecte  = Order.objects.filter(pickup_driver=d, status='done').count()
        missions_livraison = Order.objects.filter(delivery_partner=d, status='done').count()
        nb = missions_collecte + missions_livraison
        remun_collecte  = getattr(d, 'remuneration_collecte', 800) or 800
        remun_livraison = getattr(d, 'remuneration_livraison', 800) or 800
        a_payer = (missions_collecte * remun_collecte) + (missions_livraison * remun_livraison)
        livreurs_data.append({
            'id': d.id,
            'name': d.name,
            'phone': d.phone or '',
            'wave_number': getattr(d, 'wave_number', '') or '',
            'nb_missions': nb,
            'a_payer': int(a_payer),
            'deja_paye': int(Paiement.objects.filter(partenaire_id=d.id, partenaire_type='livreur').aggregate(s=Sum('montant'))['s'] or 0),
        })

    total_a_payer = sum(p['a_payer'] for p in pressings_data) + sum(l['a_payer'] for l in livreurs_data)
    nb_done = Order.objects.filter(status='done').count()

    # Retraits en attente — source de vérité : WithdrawalRequest
    from wallets.models import WithdrawalRequest
    wr_qs = WithdrawalRequest.objects.filter(
        status='pending'
    ).select_related('wallet__delivery_partner', 'wallet__laundry_partner')
    retraits = []
    for wr in wr_qs:
        w = wr.wallet
        nom = wr.get_beneficiary_display()
        phone = ''
        if getattr(w, 'delivery_partner_id', None):
            phone = getattr(w.delivery_partner, 'phone', '') or ''
        elif getattr(w, 'laundry_partner_id', None):
            phone = getattr(w.laundry_partner, 'phone', '') or ''
        retraits.append({
            'id': wr.id,
            'partenaire_nom': nom,
            'montant': float(wr.amount),
            'wave_number': phone,
            'created_at': wr.created_at.isoformat() if wr.created_at else None,
            'status': wr.status,
        })

    # Historique retraits (paid + rejected) — 20 derniers
    historique = []
    for wr in WithdrawalRequest.objects.filter(
        status__in=['paid', 'rejected']
    ).select_related('wallet__delivery_partner', 'wallet__laundry_partner').order_by('-created_at')[:20]:
        historique.append({
            'id': wr.id,
            'partenaire_nom': wr.get_beneficiary_display(),
            'montant': float(wr.amount),
            'status': wr.status,
            'created_at': wr.created_at.isoformat() if wr.created_at else None,
            'processed_at': wr.processed_at.isoformat() if wr.processed_at else None,
        })

    return Response({
        'pressings': pressings_data,
        'livreurs': livreurs_data,
        'total_a_payer': total_a_payer,
        'nb_commandes_done': nb_done,
        'retraits': retraits,
        'historique_retraits': historique,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def api_ops_enregistrer_paiement(request):
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)
    """POST /api/ops/paiements/enregistrer/ — enregistrer un paiement effectué"""
    from orders.models import Paiement

    partenaire_type = request.data.get('partenaire_type')
    partenaire_id   = request.data.get('partenaire_id')
    partenaire_nom  = request.data.get('partenaire_nom')
    montant         = request.data.get('montant', 0)
    nb_commandes    = request.data.get('nb_commandes', 0)
    wave_number     = request.data.get('wave_number', '')
    note            = request.data.get('note', '')

    try:
        # Si c'est une validation de retrait, mettre à jour WithdrawalRequest
        if note == 'Retrait valide par OPS':
            from wallets.models import WithdrawalRequest
            from django.utils import timezone
            WithdrawalRequest.objects.filter(
                id=partenaire_id, status='pending'
            ).update(status='paid', processed_at=timezone.now())
            return Response({'success': True})

        paiement = Paiement.objects.create(
            partenaire_type=partenaire_type,
            partenaire_id=partenaire_id,
            partenaire_nom=partenaire_nom,
            montant=montant,
            nb_commandes=nb_commandes,
            wave_number=wave_number,
            note=note,
        )
        return Response({'success': True, 'paiement_id': paiement.id})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([AllowAny])
def api_ops_revenus(request):
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)
    """GET /api/ops/revenus/ — revenus FAGNI"""
    from orders.models import Order
    from django.db.models import Sum
    from datetime import datetime, timedelta

    today = datetime.now().date()
    debut_semaine = today - timedelta(days=today.weekday())
    debut_mois = today.replace(day=1)

    def stats(qs):
        total_client = int(qs.aggregate(s=Sum('total_client_ttc'))['s'] or 0)
        paye_pressing = int(qs.aggregate(s=Sum('amount_laundry_partner'))['s'] or 0)
        paye_livreur = int(qs.aggregate(s=Sum('amount_driver_partner'))['s'] or 0)
        return {
            'nb': qs.count(),
            'total_client': total_client,
            'revenus_fagni': total_client - paye_pressing - paye_livreur,
            'paye_pressing': paye_pressing,
            'paye_livreur': paye_livreur,
        }

    done = Order.objects.filter(status='done')

    return Response({
        'total': stats(done),
        'semaine': stats(done.filter(updated_at__date__gte=debut_semaine)),
        'mois': stats(done.filter(updated_at__date__gte=debut_mois)),
        'en_attente': Order.objects.filter(status='pending').count(),
        'en_cours': Order.objects.filter(status='in_progress').count(),
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def api_ops_rapport_hebdo(request):
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)
    """GET /api/ops/rapport/hebdo/ — rapport de la semaine"""
    from orders.models import Order, Paiement
    from partners.models import LaundryPartner, DeliveryPartner
    from django.db.models import Sum, Count
    from datetime import datetime, timedelta

    today = datetime.now().date()
    debut_semaine = today - timedelta(days=today.weekday())
    fin_semaine = debut_semaine + timedelta(days=6)

    commandes_semaine = Order.objects.filter(
        created_at__date__gte=debut_semaine
    )
    livrees_semaine = commandes_semaine.filter(status='done')

    total_encaisse = int(livrees_semaine.aggregate(
        s=Sum('total_client_ttc'))['s'] or 0)
    # Revenu FAGNI = encaissé clients - part pressing
    _enc = livrees_semaine.aggregate(s=Sum('total_client_ttc'))['s'] or 0
    _press = livrees_semaine.aggregate(s=Sum('amount_laundry_partner'))['s'] or 0
    revenu_fagni = int(_enc - _press)
    paye_pressing = int(livrees_semaine.aggregate(
        s=Sum('amount_laundry_partner'))['s'] or 0)

    # Paiements effectués cette semaine
    paiements = Paiement.objects.filter(
        created_at__date__gte=debut_semaine
    )
    total_paye = int(paiements.aggregate(s=Sum('montant'))['s'] or 0)

    # Message WhatsApp rapport
    msg = f"""RAPPORT HEBDOMADAIRE FAGNI
Semaine du {debut_semaine.strftime('%d/%m')} au {fin_semaine.strftime('%d/%m/%Y')}

COMMANDES :
- Nouvelles : {commandes_semaine.count()}
- Livrees : {livrees_semaine.count()}
- En attente : {Order.objects.filter(status='pending').count()}

FINANCIER :
- Encaisse clients : {total_encaisse:,} FCFA
- Revenu FAGNI : {revenu_fagni:,} FCFA
- Paye pressings : {paye_pressing:,} FCFA
- Deja paye : {total_paye:,} FCFA
- Reste a payer : {(paye_pressing - total_paye):,} FCFA

Connecte-toi sur fagni-ops.vercel.app"""

    import urllib.parse
    wa_link = f"https://wa.me/2250142299949?text={urllib.parse.quote(msg)}"

    return Response({
        'semaine': str(debut_semaine),
        'commandes_nouvelles': commandes_semaine.count(),
        'commandes_livrees': livrees_semaine.count(),
        'en_attente': Order.objects.filter(status='pending').count(),
        'total_encaisse': total_encaisse,
        'revenu_fagni': revenu_fagni,
        'paye_pressing': paye_pressing,
        'total_paye': total_paye,
        'reste_a_payer': paye_pressing - total_paye,
        'wa_link': wa_link,
        'message': msg,
    })




@api_view(['GET'])
@permission_classes([AllowAny])
def api_ops_wallets(request):
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)
    """GET /api/ops/wallets/ — soldes wallet réels de tous les partenaires"""
    from wallets.models import Wallet
    wallets_data = []
    qs = Wallet.objects.filter(
        owner_type__in=['driver', 'laundry']
    ).select_related('delivery_partner', 'laundry_partner')
    for w in qs:
        if w.owner_type == 'driver' and w.delivery_partner:
            p = w.delivery_partner
            emoji = '🛵'
            type_ = 'livreur'
        elif w.owner_type == 'laundry' and w.laundry_partner:
            p = w.laundry_partner
            emoji = '🧺'
            type_ = 'pressing'
        else:
            continue
        wallets_data.append({
            'nom': p.name,
            'phone': p.phone or '',
            'wave_number': getattr(p, 'wave_number', '') or '',
            'emoji': emoji,
            'type': type_,
            'balance_disponible': float(w.balance or 0),
            'balance_securite': float(w.balance_securite or 0),
            'total': float((w.balance or 0) + (w.balance_securite or 0)),
        })
    return Response({'wallets': wallets_data})

@api_view(['GET'])
@permission_classes([AllowAny])
def api_ops_activite_jour(request):
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)
    """GET /api/ops/activite/jour/ — collectes et livraisons du jour (auto)"""
    from orders.models import Order
    from django.utils import timezone
    from django.db.models import Avg
    today = timezone.localdate()
    collectes = Order.objects.filter(
        pickup_driver__isnull=False,
        status='done',
        updated_at__date=today
    ).count()
    livraisons = Order.objects.filter(
        delivery_partner__isnull=False,
        status='done',
        updated_at__date=today
    ).count()
    incidents = Order.objects.filter(
        status='litige',
        updated_at__date=today
    ).count()
    notes = Order.objects.filter(
        rating__isnull=False,
        updated_at__date=today
    )
    nb_notes = notes.count()
    note_moyenne = notes.aggregate(m=Avg('rating'))['m'] or 0
    return Response({
        'collectes': collectes,
        'livraisons': livraisons,
        'incidents': incidents,
        'nb_notes': nb_notes,
        'note_moyenne': round(float(note_moyenne), 1),
    })

@api_view(['GET'])
@permission_classes([AllowAny])
def api_score_pressing(request, partner_id):
    """
    GET /api/ops/score/pressing/<id>/ — score pressing.
    Accessible a OPS (_check_ops) OU au partenaire proprietaire du score
    (partner_token avec pid == partner_id demande) - durci le 19 juillet
    2026, remplace l'ouverture totale (AllowAny sans verification) mise
    en place lors du fix initial du 19 juillet.
    """
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        _check_ops(request)
    except Exception:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            if payload.get('pid') != partner_id:
                return Response({'error': 'Non autorise'}, status=401)
        except Exception:
            return Response({'error': 'Non autorise'}, status=401)
    from partners.models import LaundryPartner
    from orders.models import Order
    from django.db.models import Avg, Count

    try:
        partner = LaundryPartner.objects.get(id=partner_id)
    except:
        return Response({'error': 'Pressing non trouvé'}, status=404)

    commandes = Order.objects.filter(laundry_partner=partner)
    total = commandes.count()
    done = commandes.filter(status='done').count()
    litiges = commandes.filter(status='litige').count()

    # Semaines actives
    from django.db.models.functions import TruncWeek
    semaines = commandes.filter(status='done').annotate(
        week=TruncWeek('created_at')
    ).values('week').distinct().count()

    # CA total
    from django.db.models import Sum
    ca = int(commandes.filter(status='done').aggregate(
        s=Sum('amount_laundry_partner'))['s'] or 0)

    # Calcul score /100
    score_volume    = min(30, done * 1)        # 1 pt par commande, max 30
    score_regularite = min(20, semaines * 2)   # 2 pts par semaine, max 20
    score_qualite   = max(0, 30 - litiges * 5) # -5 pts par litige, max 30
    score_ca        = min(20, ca // 50000)     # 1 pt par 50k FCFA, max 20
    score_total     = score_volume + score_regularite + score_qualite + score_ca

    # Niveau
    if score_total >= 80: niveau, badge = "Gold", "🥇"
    elif score_total >= 50: niveau, badge = "Silver", "🥈"
    elif score_total >= 20: niveau, badge = "Bronze", "🥉"
    else: niveau, badge = "Débutant", "⭐"

    return Response({
        'partner_id':    partner.id,
        'partner_name':  partner.name,
        'score':         score_total,
        'niveau':        niveau,
        'badge':         badge,
        'details': {
            'commandes_total':  total,
            'commandes_done':   done,
            'litiges':          litiges,
            'semaines_actives': semaines,
            'ca_total':         ca,
        },
        'score_detail': {
            'volume':     score_volume,
            'regularite': score_regularite,
            'qualite':    score_qualite,
            'ca':         score_ca,
        }
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def api_score_livreur(request, driver_id):
    """
    GET /api/ops/score/livreur/<id>/ — score livreur.
    Accessible a OPS (_check_ops) OU au livreur proprietaire du score
    (driver_token avec did == driver_id demande) - durci le 19 juillet
    2026, remplace l'ouverture totale (AllowAny sans verification) mise
    en place lors du fix initial du 19 juillet.
    """
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        _check_ops(request)
    except Exception:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            if payload.get('did') != driver_id:
                return Response({'error': 'Non autorise'}, status=401)
        except Exception:
            return Response({'error': 'Non autorise'}, status=401)
    from partners.models import DeliveryPartner
    from orders.models import Order
    from django.db.models import Sum

    try:
        driver = DeliveryPartner.objects.get(id=driver_id)
    except:
        return Response({'error': 'Livreur non trouvé'}, status=404)

    collectes = Order.objects.filter(pickup_driver=driver, status='done').count()
    livraisons = Order.objects.filter(pickup_driver=driver, status='done', cost_driver_delivery__gt=0).count()
    total_missions = collectes + livraisons
    litiges = Order.objects.filter(
        pickup_driver=driver, status='litige'
    ).count()

    # Semaines actives
    from django.db.models.functions import TruncWeek
    semaines = Order.objects.filter(
        pickup_driver=driver, status='done'
    ).annotate(week=TruncWeek('created_at')).values('week').distinct().count()

    # Revenus
    remun_c = getattr(driver, 'remuneration_collecte', 800) or 800
    remun_l = getattr(driver, 'remuneration_livraison', 800) or 800
    revenus = (collectes * remun_c) + (livraisons * remun_l)

    # Score /100
    score_volume    = min(30, total_missions * 1)
    score_regularite = min(20, semaines * 2)
    score_qualite   = max(0, 30 - litiges * 5)
    score_revenus   = min(20, revenus // 25000)
    score_total     = score_volume + score_regularite + score_qualite + score_revenus

    if score_total >= 80: niveau, badge = "Gold", "🥇"
    elif score_total >= 50: niveau, badge = "Silver", "🥈"
    elif score_total >= 20: niveau, badge = "Bronze", "🥉"
    else: niveau, badge = "Débutant", "⭐"

    return Response({
        'driver_id':   driver.id,
        'driver_name': driver.name,
        'score':       score_total,
        'niveau':      niveau,
        'badge':       badge,
        'details': {
            'collectes':        collectes,
            'livraisons':       livraisons,
            'total_missions':   total_missions,
            'litiges':          litiges,
            'semaines_actives': semaines,
            'revenus_total':    revenus,
        },
        'score_detail': {
            'volume':     score_volume,
            'regularite': score_regularite,
            'qualite':    score_qualite,
            'revenus':    score_revenus,
        }
    })



def _get_authenticated_identity(request):
    """Decode le JWT et retourne (type, id) de l'utilisateur authentifie, ou (None, None)."""
    import jwt
    from django.conf import settings
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token:
        return None, None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    except Exception:
        return None, None
    if 'cid' in payload:
        return 'client', payload['cid']
    if 'did' in payload:
        return 'livreur', payload['did']
    if 'pid' in payload:
        return 'pressing', payload['pid']
    return None, None


def generer_code():
    """Générer un code parrainage unique."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


@api_view(['POST'])
@permission_classes([AllowAny])
def api_creer_parrainage_v2secure(request):
    """POST /api/parrainage/creer/ — créer un lien de parrainage"""
    from orders.models import Parrainage

    parrain_type = request.data.get('parrain_type')
    parrain_id   = request.data.get('parrain_id')
    parrain_nom  = request.data.get('parrain_nom')

    if not all([parrain_type, parrain_id, parrain_nom]):
        return Response({'error': 'Données manquantes'}, status=400)

        auth_type, auth_id = _get_authenticated_identity(request)
        if auth_type is None or auth_type != parrain_type or str(auth_id) != str(parrain_id):
            return Response({'error': 'Non autorise'}, status=401)

    # Vérifier si un parrainage existe déjà pour ce parrain
    existing = Parrainage.objects.filter(
        parrain_type=parrain_type,
        parrain_id=parrain_id,
        statut='invite'
    ).first()

    if existing:
        return Response({
            'code':     existing.code_parrainage,
            'wa_link':  f"https://wa.me/?text=Rejoins FAGNI avec mon code {existing.code_parrainage} ! Blanchisserie à domicile à Abidjan. Télécharge l'app : fagni-client.vercel.app",
            'message':  'Lien existant récupéré'
        })

    # Récompenses selon type
    REMUNERATIONS = {
        'client':   {'parrain': 500,  'filleul': 500,  'actions': 1},
        'livreur':  {'parrain': 2000, 'filleul': 0,    'actions': 10},
        'pressing': {'parrain': 5000, 'filleul': 0,    'actions': 10},
    }
    remun = REMUNERATIONS.get(parrain_type, {'parrain': 500, 'filleul': 0, 'actions': 10})

    # Générer code unique
    code = generer_code()
    while Parrainage.objects.filter(code_parrainage=code).exists():
        code = generer_code()

    parrainage = Parrainage.objects.create(
        parrain_type=parrain_type,
        parrain_id=parrain_id,
        parrain_nom=parrain_nom,
        filleul_type=parrain_type,
        code_parrainage=code,
        actions_requises=remun['actions'],
        remuneration_parrain=remun['parrain'],
        remuneration_filleul=remun['filleul'],
        score_bonus=10,
        cash_active=False,
    )

    wa_text = f"Rejoins FAGNI avec mon code {code} ! Blanchisserie à domicile à Abidjan. Télécharge l'app : fagni-client.vercel.app"

    return Response({
        'code':    code,
        'wa_link': f"https://wa.me/?text={wa_text}",
        'remuneration_parrain': remun['parrain'],
        'remuneration_filleul': remun['filleul'],
        'actions_requises':     remun['actions'],
        'cash_active':          False,
        'message': 'Code parrainage créé'
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def api_stats_parrainage(request, parrain_type, parrain_id):
    """GET /api/parrainage/stats/<type>/<id>/ — stats parrainage"""
    auth_type, auth_id = _get_authenticated_identity(request)
    if auth_type is None or auth_type != parrain_type or str(auth_id) != str(parrain_id):
        return Response({'error': 'Non autorise'}, status=401)
    from orders.models import Parrainage

    parrainages = Parrainage.objects.filter(
        parrain_type=parrain_type,
        parrain_id=parrain_id
    )

    total_invites  = parrainages.count()
    total_actifs   = parrainages.filter(statut='actif').count()
    total_payes    = parrainages.filter(statut='paye').count()
    score_gagne    = total_actifs * 10
    cash_gagne     = sum(p.remuneration_parrain for p in parrainages.filter(statut__in=['actif','paye'], cash_paye=True))
    cash_en_attente= sum(p.remuneration_parrain for p in parrainages.filter(statut='actif', cash_paye=False))

    # Code parrainage principal
    premier = parrainages.first()
    code    = premier.code_parrainage if premier else None
    wa_link = f"https://wa.me/?text=Rejoins FAGNI avec mon code {code} ! fagni-client.vercel.app" if code else None

    return Response({
        'parrain_type':    parrain_type,
        'parrain_id':      parrain_id,
        'code':            code,
        'wa_link':         wa_link,
        'stats': {
            'total_invites':   total_invites,
            'total_actifs':    total_actifs,
            'total_payes':     total_payes,
            'score_gagne':     score_gagne,
            'cash_gagne':      cash_gagne,
            'cash_en_attente': cash_en_attente,
        },
        'filleuls': list(parrainages.values(
            'filleul_nom', 'filleul_phone', 'statut',
            'nb_actions', 'actions_requises', 'created_at'
        )),
        'cash_active': False,
        'message_cash': f"Récompenses cash activées quand FAGNI atteint 100 commandes/mois. Actuellement : {from_orders_count()} commandes/mois",
    })


def from_orders_count():
    from orders.models import Order
    from datetime import datetime, timedelta
    debut = datetime.now().date().replace(day=1)
    return Order.objects.filter(created_at__date__gte=debut).count()


@api_view(['POST'])
@permission_classes([AllowAny])
def api_valider_code_parrainage(request):
    """POST /api/parrainage/valider/ — filleul utilise un code"""
    from orders.models import Parrainage

    code         = request.data.get('code', '').upper().strip()
    filleul_nom  = request.data.get('filleul_nom', '')
    filleul_phone= request.data.get('filleul_phone', '')
    filleul_id   = request.data.get('filleul_id')

    try:
        p = Parrainage.objects.get(code_parrainage=code)
    except Parrainage.DoesNotExist:
        return Response({'error': 'Code invalide'}, status=404)

    if p.statut != 'invite':
        return Response({'error': 'Code déjà utilisé'}, status=400)

        auth_type, auth_id = _get_authenticated_identity(request)
        if auth_type is None or auth_type != p.filleul_type or str(auth_id) != str(filleul_id):
            return Response({'error': 'Non autorise'}, status=401)

    p.filleul_nom   = filleul_nom
    p.filleul_phone = filleul_phone
    p.filleul_id    = filleul_id
    p.statut        = 'inscrit'
    p.save()

    return Response({
        'success': True,
        'parrain_nom': p.parrain_nom,
        'remuneration_filleul': p.remuneration_filleul,
        'actions_requises': p.actions_requises,
        'message': f"Code validé ! Effectue {p.actions_requises} commandes pour activer la récompense."
    })


# ============================================================
# WALLET — DISPATCH AUTOMATIQUE APRÈS COMMANDE LIVRÉE
# ============================================================

def notify_client_whatsapp(order, message):
    """Envoyer notification WhatsApp au client."""
    import urllib.parse
    try:
        phone = order.customer.phone if order.customer else None
        if not phone:
            return None
        # Nettoyer le numéro
        phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        if phone.startswith('0'):
            phone = '225' + phone[1:]
        elif not phone.startswith('225'):
            phone = '225' + phone
        wa_link = f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"
        return wa_link
    except:
        return None



@api_view(['POST'])
@permission_classes([AllowAny])
def api_wallet_solde(request):
    """POST /api/wallet/solde/ - solde wallet partenaire/livreur, consultation directe sans jeton OPS."""
    from wallets.models import Wallet, WalletTransaction

    partner_type = request.data.get('partner_type')
    partner_id   = request.data.get('partner_id')

    try:
        if partner_type == 'pressing':
            from partners.models import LaundryPartner
            p = LaundryPartner.objects.get(id=partner_id)
            wallet, _ = Wallet.objects.get_or_create(
                laundry_partner=p,
                defaults={'currency': 'XOF', 'balance': 0}
            )
        elif partner_type == 'livreur':
            from partners.models import DeliveryPartner
            d = DeliveryPartner.objects.get(id=partner_id)
            wallet, _ = Wallet.objects.get_or_create(
                delivery_partner=d,
                defaults={'currency': 'XOF', 'balance': 0}
            )
        else:
            return Response({'error': 'Type invalide'}, status=400)

        transactions = WalletTransaction.objects.filter(
            wallet=wallet
        ).order_by('-created_at')[:20]

        # Pending OPS pour pressing
        pending_ops_total = 0
        pending_ops_orders = []
        if partner_type == 'pressing':
            from orders.models import Order
            credited_ids = set(wallet.transactions.filter(order_id__isnull=False).values_list('order_id', flat=True))
            for o in Order.objects.filter(laundry_partner_id=partner_id, status='done').order_by('-updated_at')[:30]:
                if o.id in credited_ids: continue
                amt = float(getattr(o,'amount_laundry_partner',0) or 0)
                if amt <= 0: continue
                pending_ops_total += amt
                pending_ops_orders.append({'code': getattr(o,'code','') or f'Commande #{o.id}', 'amount': amt})

        return Response({
            'solde': float(wallet.balance), 'pending_ops_total': pending_ops_total, 'pending_ops_orders': pending_ops_orders[:5],
            'currency': wallet.currency,
            'transactions': [{
                'date':        t.created_at.strftime('%d/%m/%Y %H:%M'),
                'type':        t.type,
                'direction':   t.direction,
                'montant':     float(t.amount),
                'description': t.description or '',
            } for t in transactions]
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def api_wallet_retrait(request):
    """
    POST /api/wallet/retrait/ — demande de retrait partenaire.
    Pas de _check_ops : l'app partenaire (pressing, livreur) n'a jamais
    de jeton OPS, la fonction s'identifie deja via partner_id/partner_type
    dans le payload - meme correction que api_wallet_solde (commit adec79a).

    Cree un wallets.WithdrawalRequest - source de verite UNIQUE pour les
    retraits (coherent avec laundry_wallet_dashboard/driver_wallet_dashboard
    et l'ecran OPS Centre de retraits, qui lisaient deja WithdrawalRequest
    sans jamais voir les demandes crees ici via Paiement - deux systemes
    parallelles jamais synchronises, corrige le 19 juillet).
    """
    from wallets.models import Wallet, WithdrawalRequest
    from fagni.notifications import notif_ops_retrait

    partner_type = request.data.get('partner_type')
    partner_id   = request.data.get('partner_id')
    partner_nom  = request.data.get('partner_nom', '')
    montant      = int(request.data.get('montant', 0))

    if montant < 500:
        return Response({'error': 'Montant minimum 500 FCFA'}, status=400)

    try:
        if partner_type == 'pressing':
            from partners.models import LaundryPartner
            p = LaundryPartner.objects.get(id=partner_id)
            wallet = Wallet.objects.get(laundry_partner=p)
        else:
            from partners.models import DeliveryPartner
            d = DeliveryPartner.objects.get(id=partner_id)
            wallet = Wallet.objects.get(delivery_partner=d)

        if float(wallet.balance) < montant:
            return Response({'error': 'Solde insuffisant'}, status=400)

        wr = WithdrawalRequest.objects.create(
            wallet=wallet,
            amount=montant,
            status='pending',
        )

        try:
            notif_ops_retrait(wr.get_beneficiary_display(), montant, wallet.id)
        except Exception as e:
            print(f"[RETRAIT] Erreur notif FCM: {e}")

        return Response({
            'success': True,
            'withdrawal_request_id': wr.id,
            'montant': montant,
            'solde_actuel': float(wallet.balance),
            'message': f'Demande de {montant:,} FCFA enregistree. OPS vous paiera sous 24-48h.'
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def api_partner_penalty(request, partner_id):
    """POST /api/ops/partners/<id>/penalty/ — {reason}"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from partners.models import LaundryPartner
    from partners.services import apply_partner_penalty, PENALTIES

    try:
        partner = LaundryPartner.objects.get(id=partner_id)
    except LaundryPartner.DoesNotExist:
        return Response({'error': 'Partenaire introuvable'}, status=404)

    reason = request.data.get('reason', '')
    if reason not in PENALTIES:
        return Response({
            'error': 'Raison invalide',
            'valid_reasons': list(PENALTIES.keys())
        }, status=400)

    result = apply_partner_penalty(partner, reason)
    return Response({'success': True, **result})


@api_view(['POST'])
@permission_classes([AllowAny])
def api_partner_bonus(request, partner_id):
    """POST /api/ops/partners/<id>/bonus/ — {reason}"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from partners.models import LaundryPartner
    from partners.services import apply_partner_bonus, BONUSES

    try:
        partner = LaundryPartner.objects.get(id=partner_id)
    except LaundryPartner.DoesNotExist:
        return Response({'error': 'Partenaire introuvable'}, status=404)

    reason = request.data.get('reason', '')
    if reason not in BONUSES:
        return Response({
            'error': 'Raison invalide',
            'valid_reasons': list(BONUSES.keys())
        }, status=400)

    result = apply_partner_bonus(partner, reason)
    return Response({'success': True, **result})


@api_view(['GET'])
@permission_classes([AllowAny])
def api_partner_score_history(request, partner_id):
    """GET /api/ops/partners/<id>/score-history/ — historique 30 derniers"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from partners.models import LaundryPartner, PartnerScoreHistory
    from partners.services import get_partner_badge

    try:
        partner = LaundryPartner.objects.get(id=partner_id)
    except LaundryPartner.DoesNotExist:
        return Response({'error': 'Partenaire introuvable'}, status=404)

    history = PartnerScoreHistory.objects.filter(
        partner=partner
    ).order_by('-computed_at')[:30]

    return Response({
        'partner_id':    partner.id,
        'partner_name':  partner.name,
        'current_score': partner.partner_score,
        'current_level': partner.level,
        'badge':         get_partner_badge(partner),
        'history': list(history.values(
            'score', 'level', 'score_delai', 'score_litiges',
            'score_dispo', 'score_refus', 'computed_at'
        ))
    })

@api_view(['GET'])
@permission_classes([AllowAny])
def ops_order_detail(request, order_id):
    """GET /api/ops/orders/<id>/ — fiche commande complète"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)
    try:
        from orders.models import Order, OrderItem, OrderEvidencePhoto
        o = Order.objects.select_related(
            'customer','laundry_partner','pickup_driver','delivery_partner'
        ).prefetch_related('items','evidence_photos').get(id=order_id)

        # Articles
        items = [{'designation':i.designation,'qty':i.quantity,
                  'unit_price':float(i.unit_price),'total':float(i.total)}
                 for i in o.items.all()]

        # Photos par type
        photos = {}
        for p in o.evidence_photos.all().order_by('created_at'):
            photos.setdefault(p.kind, []).append({
                'url': p.image.url if p.image else '',
                'actor': p.actor_type,
                'caption': p.caption,
                'created_at': p.created_at.strftime('%d/%m %H:%M'),
            })

        # Legs (GPS, signature, statuts collecte/livraison)
        from orders.models import DeliveryLeg
        legs_data = {}
        for leg in DeliveryLeg.objects.filter(order=o):
            legs_data[leg.leg_type] = {
                'status': leg.status,
                'driver': leg.driver.name if leg.driver else None,
                'picked_up_lat': getattr(leg, 'picked_up_lat', None),
                'picked_up_lng': getattr(leg, 'picked_up_lng', None),
                'pickup_signature': bool(getattr(leg, 'pickup_signature', None)),
                'pickup_signed_at': leg.pickup_signed_at.strftime('%d/%m %H:%M') if getattr(leg, 'pickup_signed_at', None) else None,
                'delivered_lat': getattr(leg, 'delivered_lat', None),
                'delivered_lng': getattr(leg, 'delivered_lng', None),
                'client_signature': getattr(leg, 'client_signature', None),
                'client_signed_at': leg.client_signed_at.strftime('%d/%m %H:%M') if getattr(leg, 'client_signed_at', None) else None,
            }

        # Fallback : photos historiques stockees uniquement en tag texte (avant migration)
        notes_photos = []
        for line in (o.notes or '').split('\n'):
            if line.startswith('PHOTO_'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    notes_photos.append({'type': parts[0].replace('PHOTO_', '').lower(), 'url': parts[1].strip()})

        # Timeline
        def fmt(dt): return dt.strftime('%d/%m %H:%M') if dt else None

        return Response({
            'id': o.id,
            'code': o.code,
            'status': o.status,
            'created_at': fmt(o.created_at),
            'client': {
                'name': o.customer.name if o.customer else '',
                'phone': o.customer.phone if o.customer else '',
                'address': o.pickup_address or '',
            },
            'pressing': {
                'name': o.laundry_partner.name if o.laundry_partner else '—',
                'phone': o.laundry_partner.phone if o.laundry_partner else '',
            },
            'livreur_collecte': {
                'name': o.pickup_driver.name if o.pickup_driver else '—',
                'phone': o.pickup_driver.phone if o.pickup_driver else '',
            },
            'livreur_livraison': {
                'name': o.delivery_partner.name if o.delivery_partner else '—',
                'phone': o.delivery_partner.phone if o.delivery_partner else '',
            },
            'articles': items,
            'articles_count': o.articles_count,
            'financials': {
                'total_client': float(o.total_client_ttc or 0),
                'part_pressing': float(o.amount_laundry_partner or 0),
                'livraison': float(o.delivery_fee or 0),
                'cost_livreur': int(o.cost_driver_pickup or 0) + int(o.cost_driver_delivery or 0),
                'marge_fagni': float(o.total_client_ttc or 0) - float(o.amount_laundry_partner or 0),
            },
            'timeline': {
                'commande': fmt(o.created_at),
                'collecte': fmt(o.pickup_time),
                'depot_pressing': fmt(o.dropoff_time),
                'lavage_termine': fmt(o.wash_complete_time),
                'retour_livreur': fmt(o.return_time),
                'livraison': fmt(o.delivered_time),
            },
            'photos': photos,
            'legs': legs_data,
            'notes_photos_fallback': notes_photos,
            'notes': o.notes or '',
        })
    except Order.DoesNotExist:
        return Response({'error': 'Commande introuvable'}, status=404)
    except Exception as e:
        return Response({'error': str(e)}, status=400)

@api_view(["POST"])
@permission_classes([AllowAny])
def ops_assign_return_driver(request, order_id):
    """POST /api/ops/orders/<id>/assign-return-driver/ — assigner livreur retour apres pressing PRET"""
    try:
        _check_ops(request)
    except:
        return Response({"error": "Non autorise"}, status=401)
    from orders.models import Order, DeliveryLeg
    from partners.models import DeliveryPartner
    from decimal import Decimal
    try:
        order = Order.objects.get(id=order_id)
        if not order.wash_complete_time:
            return Response({"error": "Pressing doit marquer PRET avant"}, status=400)
        driver_id = request.data.get("driver_id")
        driver = DeliveryPartner.objects.get(id=driver_id)
        from orders.config_models import GlobalPricingSettings as _GPS4
        _dap_ret = Decimal(str(_GPS4.get_solo().driver_amount_per_leg))
        leg, created = DeliveryLeg.objects.get_or_create(
            order=order, leg_type="return",
            defaults={"status": "pending", "driver_amount": _dap_ret}
        )
        assigned_now = False
        if leg.status not in ("assigned", "in_progress", "done"):
            from orders.config_models import GlobalPricingSettings as _GPS2
            _driver_amount_return = Decimal(str(_GPS2.get_solo().driver_amount_per_leg))
            leg.driver = driver
            leg.status = "assigned"
            leg.driver_amount = _driver_amount_return
            leg.save(update_fields=["driver", "status", "driver_amount"])
            assigned_now = True

        order.delivery_partner = driver
        order.cost_driver_delivery = int(_driver_amount_return)
        order.save(update_fields=["delivery_partner", "cost_driver_delivery", "updated_at"])

        # Notifications retour : une seule fois, uniquement lors de l'assignation effective.
        if assigned_now:
            _send_notif_mission(order, driver)
            _send_notif_client_livraison(order)
            try:
                from orders.models import log_event
                log_event(
                    "return.assigned",
                    order=order,
                    actor_type="ops",
                    actor_id=None,
                    driver_id=driver.id,
                    driver_name=driver.name,
                )
            except Exception:
                import logging
                logging.getLogger("fagni.ops_api").exception("Echec silencieux: return.assigned (log_event) | order_id=%s", getattr(order, "id", None))

        return Response({"success": True, "message": f"Livreur retour {driver.name} assigne"})
    except Exception as e:
        return Response({"error": str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def api_simulateur_notify(request):
    """
    POST /api/simulateur/notify?key=<SIMULATEUR_NOTIFY_KEY>
    Appele par le backend V2 (fagni-platform) apres une reservation via
    /estimation - fire-and-forget, jamais bloquant pour la reservation
    elle-meme si cet appel echoue. Exception consciente au principe
    "pas de pont automatique V1/V2" (FOS-213 section 0) : justifiee ici
    car non-critique (une simple alerte), contrairement a une dependance
    de logique metier.
    """
    key = request.GET.get('key', '') or request.data.get('key', '')
    expected = getattr(settings, 'SIMULATEUR_NOTIFY_KEY', 'fagni_simulateur_notify_2026')
    if key != expected:
        return Response({'error': 'Cle invalide'}, status=401)

    from fagni.notifications import notif_ops_retrait

    sim_id = request.data.get('sim_id', '')
    nom = request.data.get('nom', '')
    telephone = request.data.get('telephone', '')
    prix = request.data.get('prix', 0)

    try:
        notif_ops_retrait(
            f"Nouvelle reservation {nom} ({telephone})",
            prix,
            f"sim:{sim_id}",
        )
    except Exception as e:
        import logging
        logging.getLogger("fagni.ops_api").exception("Echec notif simulateur | sim_id=%s | error=%s", sim_id, str(e))

    return Response({'success': True})
