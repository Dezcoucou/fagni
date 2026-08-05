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


def _bc3_auto_assign_return_driver(order):
    """
    BC3 — auto-affectation additive du livreur retour au moment ou le
    pressing marque la commande PRET (wash_complete_time), derriere
    settings.AUTO_ASSIGN_RETURN_DRIVER (defaut False = comportement
    strictement identique a avant BC3 : la mission retour est creee mais
    jamais assignee automatiquement).

    Reutilise integralement orders.assignment.pick_best_driver (meme moteur
    que BC1 collecte et que ops_assign_return_driver) - aucune nouvelle
    regle, aucune nouvelle formule. Reproduit a l'identique les effets de
    bord de ops_assign_return_driver (driver sur la DeliveryLeg return,
    order.delivery_partner, order.cost_driver_delivery, notifications) pour
    que la mission soit exploitable par l'app livreur et que la
    reaffectation OPS reste possible ensuite.

    Ne cree jamais de 2e jambe return (get_or_create sur (order,
    leg_type='return'), deja verrouille par la contrainte DB
    uniq_leg_per_order_type). Idempotent : si order.delivery_partner est
    deja renseigne (affectation BC3 precedente ou reaffectation OPS), ne
    refait rien et ne renvoie aucune notification en double.

    Ne leve jamais d'exception : l'appelant doit pouvoir ignorer un echec
    sans jamais bloquer la transition "Pret".
    """
    import logging
    from decimal import Decimal
    from orders.assignment import pick_best_driver
    from orders.models import DeliveryLeg
    from orders.config_models import GlobalPricingSettings
    from orders.ops_api import _send_notif_mission, _send_notif_client_livraison

    logger = logging.getLogger("fagni.bc3.assignment")

    result = {"driver_assigned": False}

    if getattr(order, "status", None) == "canceled":
        return result

    # Deja assigne (appel BC3 precedent sur repetition du statut PRET, ou
    # reaffectation OPS entre-temps) : ne jamais re-choisir ni re-notifier.
    if getattr(order, "delivery_partner_id", None):
        return result

    try:
        driver, reason = pick_best_driver(order)
        if not driver:
            logger.warning(
                "BC3 livreur retour: aucun candidat | order_id=%s | raison=%s",
                order.id, reason or "raison non renseignee par le moteur",
            )
            return result

        logger.info(
            "BC3 livreur retour: candidat trouve | order_id=%s | driver_id=%s | driver_name=%s",
            order.id, driver.id, driver.name,
        )

        driver_amount = Decimal(str(GlobalPricingSettings.get_solo().driver_amount_per_leg))

        leg, _created = DeliveryLeg.objects.get_or_create(
            order=order, leg_type="return",
            defaults={"status": "pending", "driver_amount": driver_amount},
        )

        assigned_now = False
        if leg.status not in ("assigned", "in_progress", "done"):
            leg.driver = driver
            leg.status = "assigned"
            leg.driver_amount = driver_amount
            leg.save(update_fields=["driver", "status", "driver_amount"])
            assigned_now = True

        order.delivery_partner = driver
        order.cost_driver_delivery = int(driver_amount)
        order.save(update_fields=["delivery_partner", "cost_driver_delivery"])

        result["driver_assigned"] = True

        if assigned_now:
            try:
                _send_notif_mission(order, driver)
                _send_notif_client_livraison(order)
            except Exception:
                logger.exception("BC3 notification en echec | order_id=%s", order.id)

            try:
                from orders.models import log_event
                log_event(
                    "return.assigned", order=order,
                    actor_type="system", actor_id=None,
                    driver_id=driver.id, driver_name=driver.name,
                )
            except Exception:
                logger.exception("BC3 log_event en echec | order_id=%s", order.id)

    except Exception:
        logger.exception(
            "BC3 livreur retour: EXCEPTION moteur d'affectation | order_id=%s",
            getattr(order, "id", None),
        )

    return result


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



def _get_photos_from_notes(notes):
    result = {}
    for raw in (notes or "").split("\n"):
        if raw.startswith("PHOTO_"):
            parts = raw.split(":", 1)
            if len(parts) == 2:
                key = parts[0].replace("PHOTO_", "").lower()
                result[key] = parts[1].strip()
    return result

@api_view(["GET"])
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
        'photos': _get_photos_from_notes(o.notes),
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
        'ready': 'ready',
        'in_progress': 'in_progress',
        'done': 'done',
        'pending': 'pending',
    }
    new_status = STATUS_MAP.get(raw_status, raw_status)

    # Le pressing ne doit jamais pouvoir finaliser directement la commande :
    # Order.status ne passe a "done" que via sync_order_status_from_legs,
    # quand les DeliveryLeg pickup ET return sont toutes deux "done".
    if new_status == 'done':
        return Response({
            'error': 'statut_interdit',
            'message': 'Le pressing ne peut pas terminer directement la commande.',
        }, status=400)

    ALLOWED = ['in_progress', 'pending', 'ready']
    if new_status not in ALLOWED:
        return Response({
            'error': 'statut_invalide',
            'received': raw_status,
            'mapped': new_status,
            'choices': ALLOWED,
        }, status=400)

    from orders.models import DeliveryLeg

    if new_status == 'ready':
        pickup_done = DeliveryLeg.objects.filter(
            order=order, leg_type='pickup', status='done',
        ).exists()
        if not pickup_done:
            return Response({
                'error': 'pickup_non_termine',
                'message': "La collecte (DeliveryLeg pickup) n'est pas encore terminee.",
            }, status=409)

    # Mode terrain MVP : la blanchisserie peut confirmer la réception du sac.
    # On ne bloque pas ici sur paiement ou note DEPOSE_PRESSING, car ces contrôles
    # peuvent empêcher le test opérationnel alors que le sac est physiquement reçu.

    order.status = new_status
    order.save(update_fields=['status', 'updated_at'])
    if new_status == 'ready':
        from django.utils import timezone
        from decimal import Decimal
        from orders.config_models import GlobalPricingSettings
        _driver_amount = Decimal(str(GlobalPricingSettings.get_solo().driver_amount_per_leg))
        Order.objects.filter(pk=order.pk).update(wash_complete_time=timezone.now())
        DeliveryLeg.objects.get_or_create(
            order=order, leg_type='return',
            defaults={'status': 'pending', 'driver_amount': _driver_amount}
        )
        # Sprint P0, BC3 — auto-affectation additive du livreur retour,
        # desactivee par defaut (AUTO_ASSIGN_RETURN_DRIVER). Flag off :
        # comportement strictement identique a avant (mission retour creee
        # mais jamais assignee automatiquement).
        if getattr(settings, "AUTO_ASSIGN_RETURN_DRIVER", False):
            try:
                order.refresh_from_db()
                _bc3_auto_assign_return_driver(order)
            except Exception:
                import logging
                logging.getLogger("fagni.orders.partner_api").exception(
                    "BC3 auto-affectation retour en echec | order_id=%s", order.id
                )
    # Notifier le client que son linge est prêt
    try:
        from fagni.notifications import notif_client_pret
        from orders.models import FCMToken
        customer = getattr(order, 'customer', None)
        if customer:
            t = FCMToken.objects.filter(user_type='client', user_id=customer.id).first()
            if t:
                notif_client_pret(t.token, order.code or str(order.id))
    except Exception:
        import logging
        logging.getLogger("fagni.orders.partner_api").exception("Exception silencieuse (auto-log) - fichier=orders/partner_api.py ligne=166")
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

@api_view(['POST'])
@permission_classes([AllowAny])
def partner_upload_photo(request, order_id):
    try:
        partner = _get_partner(request)
    except:
        return Response({'error': 'Non autorise'}, status=401)
    try:
        from orders.models import Order, OrderPhoto
        order = Order.objects.get(id=order_id, laundry_partner=partner)
        photo_data = request.data.get('photo', '')
        photo_type = request.data.get('type', 'before')
        if not photo_data:
            return Response({'error': 'Photo manquante'}, status=400)
        # Sauvegarder en base
        OrderPhoto.objects.update_or_create(
            order=order, photo_type=photo_type,
            defaults={'photo_data': photo_data[:500000], 'uploaded_by': 'partner'}
        )
        return Response({'success': True, 'type': photo_type})
    except Exception as e:
        return Response({'error': str(e)}, status=400)
