import os
"""API Livreur FAGNI"""
import jwt
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

def credit_wallet(driver, amount_fcfa, order, description):
    """
    Crédite le wallet livreur selon le modèle Fonds Sécurité FAGNI : 700 FCFA disponible + 125 FCFA sécurité sur une mission de 800 FCFA.

    Version ledger V2 :
    - atomique
    - idempotente
    - crée une WalletTransaction réelle
    - évite le double crédit d'une même mission
    """
    try:
        from decimal import Decimal, ROUND_HALF_UP
        from django.db import transaction
        from wallets.models import Wallet, WalletTransaction

        amount = Decimal(str(amount_fcfa)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amount <= 0:
            return None

        order_id = getattr(order, "id", None)
        code = getattr(order, "code", "") or str(order_id or "NOORDER")

        desc_lower = (description or "").lower()
        if "collecte" in desc_lower:
            mission_type = "pickup"
            tx_label = "MISSION_PICKUP"
        elif "livraison" in desc_lower:
            mission_type = "delivery"
            tx_label = "MISSION_DELIVERY"
        else:
            mission_type = "mission"
            tx_label = "MISSION"

        # Idempotence forte V3 : chaque crédit est rattaché à la DeliveryLeg réelle.
        # pickup   -> DeliveryLeg leg_type="pickup"
        # delivery -> DeliveryLeg leg_type="return"
        leg = None
        leg_type = None
        if mission_type == "pickup":
            leg_type = "pickup"
        elif mission_type == "delivery":
            leg_type = "return"

        if leg_type and order_id:
            try:
                from orders.models import DeliveryLeg
                leg = (
                    DeliveryLeg.objects
                    .filter(order=order, leg_type=leg_type)
                    .order_by("-id")
                    .first()
                )
            except Exception:
                leg = None

        if leg is not None:
            idempotency_key = f"driver:{driver.id}:leg:{leg.id}:pilot_v2"
        else:
            # Fallback compatibilité si aucune jambe n'existe encore
            idempotency_key = f"driver:{driver.id}:order:{order_id}:mission:{mission_type}:pilot_v2"

        with transaction.atomic():
            wallet, _ = Wallet.objects.select_for_update().get_or_create(
                delivery_partner=driver,
                owner_type="driver",
                defaults={
                    "currency": "XOF",
                    "balance": Decimal("0.00"),
                    "balance_securite": Decimal("0.00"),
                }
            )

            existing = WalletTransaction.objects.filter(
                idempotency_key=idempotency_key
            ).first()

            if existing:
                if existing.amount == Decimal("800.00"):
                    disponible = Decimal("700")
                    securite = Decimal("125")
                else:
                    disponible = (existing.amount * Decimal("0.80")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                    securite = existing.amount - disponible
                return {
                    "disponible": int(disponible),
                    "securite": int(securite),
                    "already_credited": True,
                    "transaction_id": existing.id,
                    "idempotency_key": idempotency_key,
                }

            # Modèle Fonds Sécurité FAGNI V1 :
            # Sur 800 FCFA par tronçon :
            # - 700 FCFA disponibles immédiatement
            # - 100 FCFA épargne sécurité livreur
            # - 25 FCFA abondement FAGNI
            if amount == Decimal("800.00"):
                disponible = Decimal("700")
                securite = Decimal("125")
            else:
                disponible = (amount * Decimal("0.80")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                securite = amount - disponible

            wallet.balance = (wallet.balance + disponible).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            wallet.balance_securite = (wallet.balance_securite + securite).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            wallet.save(update_fields=["balance", "balance_securite", "updated_at"])

            tx = WalletTransaction.create_tx(
                wallet=wallet,
                order=order,
                leg=leg,
                type=WalletTransaction.TxType.CREDIT,
                direction=WalletTransaction.TxDirection.IN,
                amount=amount,
                description=f"{tx_label} | {description or code} | disponible={int(disponible)} | securite={int(securite)} | pricing=pilot_v2",
                idempotency_key=idempotency_key,
                allow_orphan=False,
            )

            return {
                "disponible": int(disponible),
                "securite": int(securite),
                "already_credited": False,
                "transaction_id": tx.id if tx else None,
                "idempotency_key": idempotency_key,
            }

    except Exception as e:
        import logging
        logging.getLogger("wallet_security").exception("credit_wallet failed: %s", e)
        return None


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
    """GET /api/driver/missions/ — missions du livreur depuis DeliveryLeg."""
    try:
        driver = _get_driver(request)
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import DeliveryLeg

    legs = (
        DeliveryLeg.objects
        .select_related('order', 'order__customer', 'order__laundry_partner', 'driver')
        .filter(driver=driver)
        .exclude(status__in=['done', 'canceled'])
        .order_by('id')[:20]
    )

    result = []
    for leg in legs:
        o = leg.order
        customer = getattr(o, 'customer', None)
        laundry = getattr(o, 'laundry_partner', None)

        is_pickup = leg.leg_type == 'pickup'
        pickup_address = getattr(o, 'pickup_address', '') or getattr(customer, 'address', '') or ''
        delivery_address = getattr(o, 'delivery_address', '') or getattr(customer, 'address', '') or ''

        result.append({
            'mission_id':      leg.id,
            'leg_id':          leg.id,
            'order_id':        o.id,
            'order_code':      o.code or str(o.id),
            'mission_type':    'pickup' if is_pickup else 'delivery',
            'leg_type':        leg.leg_type,
            'status':          leg.status,
            'zone':            (pickup_address or delivery_address or 'Abidjan').split(',')[0],
            'pickup_address':  pickup_address,
            'partner_name':    laundry.name if laundry else '',
            'partner_address': laundry.address if laundry else '',
            'partner_lat':     float(getattr(laundry, 'latitude', None)) if laundry and getattr(laundry, 'latitude', None) else None,
            'partner_lng':     float(getattr(laundry, 'longitude', None)) if laundry and getattr(laundry, 'longitude', None) else None,
            'delivery_address': delivery_address,
            'delivery_lat':    float(getattr(o, 'delivery_lat', None)) if getattr(o, 'delivery_lat', None) else None,
            'delivery_lng':    float(getattr(o, 'delivery_lng', None)) if getattr(o, 'delivery_lng', None) else None,
            'pickup_lat':      float(getattr(o, 'pickup_lat', None)) if getattr(o, 'pickup_lat', None) else None,
            'pickup_lng':      float(getattr(o, 'pickup_lng', None)) if getattr(o, 'pickup_lng', None) else None,
            'bag_size':        getattr(o, 'bag_size', '') or '',
            'order_status':    getattr(o, 'status', ''),
            'driver_amount':   float(getattr(leg, 'driver_amount', 0) or 0),
            'created_at':      o.created_at.isoformat() if getattr(o, 'created_at', None) else None,
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

        from orders.models import Order as _Order
        # C1 — Prix verrouillé à la création. articles_count = traçabilité uniquement.
        _Order.objects.filter(pk=order.pk).update(
            articles_count=articles_count,
        )


        # Marquer la mission pickup comme terminée
        try:
            from orders.models import DeliveryLeg, sync_order_status_from_legs
            pickup_leg = DeliveryLeg.objects.filter(
                order=order,
                leg_type='pickup',
                driver=driver,
            ).first() or DeliveryLeg.objects.filter(
                order=order,
                leg_type='pickup',
            ).first()

            if pickup_leg:
                pickup_leg.driver = driver
                pickup_leg.status = 'done'
                if hasattr(pickup_leg, 'finished_at'):
                    pickup_leg.finished_at = timezone.now()
                    pickup_leg.save(update_fields=['driver', 'status', 'finished_at'])
                else:
                    pickup_leg.save(update_fields=['driver', 'status'])

            sync_order_status_from_legs(order, save=True)
        except Exception:
            pass

        # Event logging LOT1
        try:
            from orders.models import log_event
            log_event(
                "pickup.done", order=order,
                actor_type="driver", actor_id=driver.id,
                articles_count=articles_count,
            )
        except Exception:
            pass

        # Wallet livreur — collecte 800 FCFA
        _pickup_amount = int(getattr(order, "cost_driver_pickup", 800) or 800)
        _wallet_result = credit_wallet(
            driver, _pickup_amount, order,
            f"Collecte commande {order.code}"
        )
        # wave_link stocké dans notes pour compatibilité
        try:
            from orders.models import Order as _OW
            _OW.objects.filter(pk=order.pk).update(driver_wallet_credited=True)
        except Exception:
            pass
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
        'payment_status': order.payment_status,
            'wave_link':      wave_link_url,
        })
    except Exception as e:
        import traceback
        return Response({'error': str(e), 'traceback': traceback.format_exc()}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def driver_delivery_proof(request, order_id):
    """POST /api/driver/orders/<id>/delivery-proof/
    C2 — Sans OTP. Preuve = photo + prenom client + GPS.
    """
    try:
        driver = _get_driver(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)

    from django.utils import timezone
    from orders.models import Order, DeliveryLeg, OrderEvidencePhoto
    try:
        order = Order.objects.get(id=order_id)
        leg = DeliveryLeg.objects.filter(order=order, leg_type='return').first()
        if not leg:
            return Response({'error': 'Mission retour introuvable'}, status=404)
        if leg.driver_id and leg.driver_id != driver.id:
            return Response({'error': 'Mission affectee a un autre livreur'}, status=403)

        client_name = (request.data.get('client_name') or '').strip()
        photo_b64   = (request.data.get('photo') or '').strip()
        lat = request.data.get('lat')
        lng = request.data.get('lng')

        if not client_name:
            return Response({'error': 'Prenom/nom du client requis'}, status=400)
        if not photo_b64:
            return Response({'error': 'Photo de remise obligatoire'}, status=400)

        now = timezone.now()

        try:
            import cloudinary.uploader as cu
            result = cu.upload(photo_b64, folder='orders/delivery', public_id=f'delivery_{order.id}_{int(now.timestamp())}')
            photo_url = result.get('secure_url', '')
            OrderEvidencePhoto.objects.create(
                order=order, leg=leg,
                actor_type='driver', actor_id=driver.id,
                kind='delivery_to_client',
                image=photo_url,
                caption=f'Remis a : {client_name}',
            )
        except Exception:
            pass

        leg.client_signature = client_name
        leg.client_signed_at = now
        leg.driver = driver
        leg.status = 'done'
        if hasattr(leg, 'finished_at'):
            leg.finished_at = now
        try:
            if lat not in [None, '']:
                leg.delivered_lat = float(lat)
            if lng not in [None, '']:
                leg.delivered_lng = float(lng)
        except Exception:
            pass

        update_fields = ['client_signature', 'client_signed_at', 'driver', 'status']
        if hasattr(leg, 'finished_at'): update_fields.append('finished_at')
        if hasattr(leg, 'delivered_lat'): update_fields += ['delivered_lat', 'delivered_lng']
        leg.save(update_fields=update_fields)

        from orders.models import sync_order_status_from_legs
        Order.objects.filter(pk=order.pk).update(delivered_time=now)
        sync_order_status_from_legs(order, save=True)

        _delivery_amount = int(getattr(order, 'cost_driver_delivery', 800) or 800)
        credit_wallet(driver, _delivery_amount, order, f"Livraison commande {order.code}")

        try:
            from orders.models import Order as _OW
            _OW.objects.filter(pk=order.pk).update(driver_wallet_credited=True)
        except Exception:
            pass
        return Response({
            'success': True,
            'message': f'Livraison confirmee — remis a {client_name}',
            'client_name': client_name,
            'delivered_time': now.isoformat(),
        })
    except Exception as e:
        import traceback
        return Response({'error': str(e), 'traceback': traceback.format_exc()}, status=400)

def driver_confirm_delivery(request, order_id):
    """POST /api/driver/orders/<id>/delivery/ — confirmer livraison"""
    try:
        driver = _get_driver(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order, DeliveryLeg, sync_order_status_from_legs
    from django.utils import timezone
    try:
        order = Order.objects.get(id=order_id)

        # Ne jamais forcer order.status='done' directement.
        # La commande devient done uniquement si pickup DONE + return DONE.
        leg, _ = DeliveryLeg.objects.get_or_create(
            order=order,
            leg_type='return',
            defaults={'driver': driver, 'status': 'pending'},
        )
        if not leg.driver_id:
            leg.driver = driver
        leg.status = 'done'
        if hasattr(leg, 'finished_at'):
            leg.finished_at = timezone.now()
            leg.save(update_fields=['driver', 'status', 'finished_at'])
        else:
            leg.save(update_fields=['driver', 'status'])

        notes = order.notes or ''
        order.notes = notes + f'\nLIVRAISON:Confirmée par livreur {driver.name}'
        order.save(update_fields=['notes', 'updated_at'])

        sync_order_status_from_legs(order, save=True)

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

        # Wallet livreur — livraison 800 FCFA
        credit_wallet(
            driver, int(getattr(order, "cost_driver_delivery", 800) or 800), order,
            f"Livraison commande {order.code}"
        )

        try:
            from orders.models import Order as _OW
            _OW.objects.filter(pk=order.pk).update(driver_wallet_credited=True)
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
    """POST /api/driver/orders/<id>/dropoff/ — C5 : livreur depose au pressing"""
    try:
        driver = _get_driver(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)

    from django.utils import timezone
    from orders.models import Order, DeliveryLeg, OrderEvidencePhoto, sync_order_status_from_legs
    try:
        order = Order.objects.get(id=order_id)
        now = timezone.now()
        photo_b64 = (request.data.get('photo') or '').strip()

        # C5 — dropoff_time obligatoire
        Order.objects.filter(pk=order.pk).update(dropoff_time=now)

        # Photo dépôt pressing (obligatoire)
        if photo_b64:
            try:
                import cloudinary.uploader as cu
                result = cu.upload(photo_b64, folder='orders/dropoff', public_id=f'dropoff_{order.id}_{int(now.timestamp())}')
                OrderEvidencePhoto.objects.create(
                    order=order,
                    actor_type='driver',
                    actor_id=driver.id,
                    kind='dropoff_to_laundry',
                    image=result.get('secure_url', ''),
                    caption=f'Depose par {driver.name}',
                )
            except Exception:
                pass

        # Marquer leg pickup done
        pickup_leg = DeliveryLeg.objects.filter(
            order=order, leg_type='pickup'
        ).first()
        if pickup_leg and pickup_leg.status != 'done':
            pickup_leg.driver = driver
            pickup_leg.status = 'done'
            if hasattr(pickup_leg, 'finished_at'):
                pickup_leg.finished_at = now
            pickup_leg.save(update_fields=['driver', 'status'] + (['finished_at'] if hasattr(pickup_leg, 'finished_at') else []))

        sync_order_status_from_legs(order, save=True)

        # Note dans la commande
        notes = order.notes or ''
        Order.objects.filter(pk=order.pk).update(
            notes=notes + f'\nDEPOSE_PRESSING:{driver.name} {now.strftime("%d/%m %H:%M")}'
        )

        return Response({
            'success': True,
            'message': 'Depot au pressing confirme',
            'dropoff_time': now.isoformat(),
        })
    except Exception as e:
        import traceback
        return Response({'error': str(e), 'traceback': traceback.format_exc()}, status=400)



@api_view(['GET'])
@permission_classes([AllowAny])
def driver_wallet(request):
    """GET /api/driver/wallet/ — solde et historique du livreur"""
    try:
        driver = _get_driver(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from wallets.models import Wallet
        from django.utils import timezone
        from datetime import timedelta

        # Wallet du livreur
        wallet, _ = Wallet.objects.get_or_create(
            delivery_partner=driver,
            owner_type='driver',
            defaults={'currency': 'XOF'}
        )

        # Prochain déblocage = lundi prochain
        today = timezone.now()
        days_ahead = 7 - today.weekday() if today.weekday() != 0 else 7
        lundi = today + timedelta(days=days_ahead)

        # Historique réel depuis le ledger wallet
        from wallets.models import WalletTransaction

        recent_txs = (
            WalletTransaction.objects
            .filter(wallet=wallet, direction="in")
            .select_related("order", "leg")
            .order_by("-created_at")[:20]
        )

        transactions = []
        for tx in recent_txs:
            amount = int(tx.amount or 0)
            if amount <= 0:
                continue

            if amount == 800:
                disponible = 700
                securite = 125
            else:
                disponible = int(amount * 0.8)
                securite = amount - disponible

            order = getattr(tx, "order", None)
            leg = getattr(tx, "leg", None)
            leg_type = getattr(leg, "leg_type", "") or ""
            label = "Collecte" if leg_type == "pickup" else "Livraison" if leg_type == "return" else "Mission"

            transactions.append({
                'description': f'{label} commande {getattr(order, "code", "") or getattr(order, "id", "")}',
                'montant': amount,
                'disponible': disponible,
                'securite': securite,
                'date': tx.created_at.strftime('%d/%m/%Y %H:%M') if getattr(tx, "created_at", None) else '',
            })

        # MVP terrain : missions terminées mais non encore créditées dans le wallet
        from orders.models import DeliveryLeg
        credited_leg_ids = set(
            wallet.transactions.filter(leg_id__isnull=False)
            .values_list("leg_id", flat=True)
        )

        pending_ops_total = 0
        pending_ops_missions = []
        done_legs = (
            DeliveryLeg.objects
            .filter(driver=driver, status="done")
            .select_related("order")
            .order_by("-finished_at")[:50]
        )

        for leg in done_legs:
            if leg.id in credited_leg_ids:
                continue
            amount = int(getattr(leg, "driver_amount", 0) or 0)
            if amount <= 0:
                continue
            pending_ops_total += amount
            order = getattr(leg, "order", None)
            pending_ops_missions.append({
                "order_id": getattr(order, "id", None),
                "code": getattr(order, "code", "") or f"Commande #{getattr(order, 'id', '')}",
                "leg_type": leg.leg_type,
                "amount": amount,
                "status": "pending_ops",
                "label": "En attente OPS",
            })

        return Response({
            "balance": float(wallet.balance),
            "balance_securite": float(wallet.balance_securite),
            "total": float(wallet.balance + wallet.balance_securite),
            "pending_ops_total": pending_ops_total,
            "pending_ops_missions": pending_ops_missions[:10],
            "available_label": "Disponible",
            "pending_label": "En attente OPS",
            "prochain_deblocage": lundi.strftime("lundi %d/%m"),
            "transactions": transactions[:10],
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def driver_toggle_status(request):
    """POST /api/driver/status/ — {is_online: true/false}"""
    try:
        driver = _get_driver(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from django.utils import timezone
        is_online = request.data.get('is_online', False)
        driver.is_online = bool(is_online)
        driver.went_online_at = timezone.now() if is_online else None
        driver.save(update_fields=['is_online', 'went_online_at'])

        return Response({
            'success': True,
            'is_online': driver.is_online,
            'message': 'En ligne ✅' if driver.is_online else 'Hors ligne',
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([AllowAny])
def driver_pending_mission(request):
    """GET /api/driver/pending/ — première mission active du livreur depuis DeliveryLeg."""
    try:
        driver = _get_driver(request)
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from orders.models import DeliveryLeg

        leg = (
            DeliveryLeg.objects
            .select_related('order', 'order__customer', 'order__laundry_partner', 'driver')
            .filter(driver=driver, status__in=['pending', 'assigned', 'in_progress'])
            .exclude(status='canceled')
            .order_by('id')
            .first()
        )

        if not leg:
            return Response({'mission': None})

        order = leg.order
        customer = getattr(order, 'customer', None)
        pickup_address = getattr(order, 'pickup_address', '') or getattr(customer, 'address', '') or ''
        delivery_address = getattr(order, 'delivery_address', '') or getattr(customer, 'address', '') or ''
        is_pickup = leg.leg_type == 'pickup'

        return Response({
            'mission': {
                'id': leg.id,
                'leg_id': leg.id,
                'order_id': order.id,
                'code': order.code or f'#{order.id}',
                'mission_type': 'pickup' if is_pickup else 'delivery',
                'leg_type': leg.leg_type,
                'status': leg.status,
                'bag_size': getattr(order, 'bag_size', '') or '',
                'pickup_address': pickup_address,
                'delivery_address': delivery_address,
                'zone': (pickup_address or delivery_address or 'Abidjan').split(',')[0],
                'total': float(getattr(order, 'total_client_ttc', 0) or getattr(order, 'total', 0) or 0),
                'created_at': order.created_at.strftime('%d/%m %H:%M') if getattr(order, 'created_at', None) else '',
                'gain_collecte': int(getattr(leg, 'driver_amount', 0) or 0),  # compat ancien frontend
                'gain_mission': int(getattr(leg, 'driver_amount', 0) or 0),
                'driver_amount': float(getattr(leg, 'driver_amount', 0) or 0),
            }
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def driver_copilote(request):
    """POST /api/driver/copilote/ — {messages: [], driver_name: str}"""
    try:
        driver = _get_driver(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        import anthropic
        messages = request.data.get('messages', [])
        
        client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))
        
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=f"""Tu es le Copilote FAGNI, assistant IA pour les livreurs du service pressing à domicile à Abidjan.
Le livreur s'appelle {driver.name}.

CONTEXTE FAGNI :
- Wallet : 700 FCFA disponible + 125 FCFA Fonds Sécurité sur une mission de 800 FCFA
- Niveaux : Bronze (0-60pts) → Silver (60-80pts) → Gold (80-100pts)
- Missions : collecte client → dépôt pressing → livraison retour
- Photos obligatoires avant collecte + après livraison
- Scellé FAGNI numéroté obligatoire sur chaque sac
- OPS joignable : +225 01 42 29 99 49
- Paiement via Wave uniquement
- En cas de client absent : appeler OPS, ne pas laisser le sac

Réponds en français, court et pratique. Utilise des emojis. Sois encourageant.""",
            messages=messages
        )
        
        return Response({'reply': response.content[0].text})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def api_driver_profil_update(request):
    """POST /api/driver/profil/update/ — mettre à jour wave_number"""
    token = request.headers.get('Authorization','').replace('Bearer ','')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from partners.models import DeliveryPartner
        driver = DeliveryPartner.objects.get(id=payload['driver_id'])
        wave = request.data.get('wave_number','').strip()
        if wave:
            driver.wave_number = wave
            driver.save(update_fields=['wave_number'])
        return Response({'success': True, 'wave_number': driver.wave_number})
    except Exception as e:
        return Response({'error': str(e)}, status=400)
