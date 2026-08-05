import os
"""API Livreur FAGNI"""
import jwt
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

def credit_wallet(driver, amount_fcfa, order, description):
    """
    Crédite le wallet livreur selon le modèle Fonds Sécurité FAGNI : disponible = montant - 100 FCFA | sécurité = 125 FCFA (100F livreur + 25F FAGNI).

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
                # PILOTE : FSS desactive - 100% disponible
                disponible = existing.amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                securite = Decimal("0")
                return {
                    "disponible": int(disponible),
                    "securite": int(securite),
                    "already_credited": True,
                    "transaction_id": existing.id,
                    "idempotency_key": idempotency_key,
                }

            # Modèle Fonds Sécurité & Solidarité FAGNI :
            # Pour toute mission >= 125 FCFA :
            # - 100 FCFA cotisation sécurité livreur
            # - 25 FCFA abondement FAGNI
            # - disponible = montant mission - 100 FCFA
            # - sécurité = 125 FCFA
            # PILOTE : FSS desactive - 100% remuneration
            from orders.config_models import GlobalPricingSettings
            _cfg_d = GlobalPricingSettings.get_solo()
            # Activation : 10 livreurs + 100 commandes
            disponible = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            securite = Decimal("0")

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
        .exclude(status__in=['done', 'canceled', 'pending'])
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
            'articles_count': sum(it.quantity for it in o.items.all()) or int(o.articles_count or 0),
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

        # Garde-fou : un livreur ne peut confirmer que la mission de
        # collecte qui lui est reellement affectee. Sans cette verification,
        # un livreur dont la mission a ete reaffectee (OPS ou BC3) pouvait,
        # depuis un ecran reste ouvert, reprendre silencieusement la
        # collecte a la place du livreur reellement affecte (audit de
        # stabilite, lot 3).
        from orders.models import DeliveryLeg as _DeliveryLegCheck
        existing_pickup_leg = _DeliveryLegCheck.objects.filter(order=order, leg_type='pickup').first()
        if existing_pickup_leg and existing_pickup_leg.driver_id and existing_pickup_leg.driver_id != driver.id:
            return Response({'error': 'Mission de collecte affectee a un autre livreur'}, status=403)

        articles_count = request.data.get('articles_count', 0)
        try: articles_count = int(articles_count)
        except: articles_count = 0
        # Controle divergence articles - ADR-023 (evolution metier, 7 juillet 2026)
        # ADR-001 (prix verrouille) reste intact : total_client_ttc n'est jamais modifie.
        # Les ecarts sont geres par ajustement explicite et tracable, jamais par reecriture silencieuse.
        expected = sum(it.quantity for it in order.items.all()) or int(order.articles_count or 0)
        count_diff = articles_count - expected

        if expected > 0 and count_diff != 0:
            from orders.config_models import GlobalPricingSettings
            from decimal import Decimal
            prix_article = Decimal(str(GlobalPricingSettings.get_solo().prix_article_fcfa or 500))
            adjustment_note = ''

            if count_diff < 0:
                montant_credit = abs(count_diff) * prix_article
                try:
                    from wallets.services import get_or_create_wallet_for_customer, credit_wallet
                    wallet = get_or_create_wallet_for_customer(order.customer)
                    credit_wallet(
                        wallet, montant_credit,
                        description=f"Ajustement après collecte — {abs(count_diff)} article(s) manquant(s) sur commande {order.code}",
                        order=order, tx_type='collection_adjustment',
                        idempotency_key=f"collection_adjustment_{order.id}",
                    )
                    adjustment_note = f"AJUSTEMENT:deficit|{expected}|{articles_count}|{montant_credit}|credit_wallet"
                except Exception:
                    import logging
                    logging.getLogger("fagni.driver_api").exception(
                        "Echec credit wallet ajustement collecte order_id=%s", order.id
                    )
                    adjustment_note = f"AJUSTEMENT:deficit|{expected}|{articles_count}|{montant_credit}|ECHEC_CREDIT"
            else:
                montant_complement = count_diff * prix_article
                adjustment_note = f"AJUSTEMENT:exces|{expected}|{articles_count}|{montant_complement}|complement_requis"

            order.notes = (order.notes or '') + f'\n{adjustment_note}'
            order.save(update_fields=['notes', 'updated_at'])

            try:
                from orders.models import log_event
                log_event(
                    "ORDER_RECALCULATED_AFTER_COLLECTION", order=order,
                    actor_type="driver", actor_id=driver.id,
                    declared_quantity=expected, collected_quantity=articles_count,
                    diff=count_diff,
                )
            except Exception:
                import logging
                logging.getLogger("fagni.driver_api").exception(
                    "Echec log_event ORDER_RECALCULATED_AFTER_COLLECTION order_id=%s", order.id
                )
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


        # Marquer la mission pickup comme collectée chez le client
        try:
            from django.utils import timezone
            from orders.models import DeliveryLeg, sync_order_status_from_legs
            pickup_leg = DeliveryLeg.objects.filter(
                order=order, leg_type='pickup', driver=driver,
            ).first() or DeliveryLeg.objects.filter(
                order=order, leg_type='pickup',
            ).first()
            if pickup_leg:
                now = timezone.now()
                pickup_leg.driver = driver
                pickup_leg.status = 'in_progress'
                update_fields = ['driver', 'status']
                if hasattr(pickup_leg, 'started_at'):
                    pickup_leg.started_at = now
                    update_fields.append('started_at')

                # POP (Proof of Pickup) - GPS + signature optionnels (MVP)
                pop_lat = request.data.get('lat')
                pop_lng = request.data.get('lng')
                pop_signature = request.data.get('signature')
                try:
                    if pop_lat not in [None, '']:
                        pickup_leg.picked_up_lat = float(pop_lat)
                        update_fields.append('picked_up_lat')
                    if pop_lng not in [None, '']:
                        pickup_leg.picked_up_lng = float(pop_lng)
                        update_fields.append('picked_up_lng')
                except (TypeError, ValueError):
                    import logging
                    logging.getLogger("fagni.orders.driver_api").exception("Exception silencieuse (auto-log) - fichier=orders/driver_api.py ligne=322")
                if pop_signature:
                    pickup_leg.pickup_signature = pop_signature
                    pickup_leg.pickup_signed_at = now
                    update_fields += ['pickup_signature', 'pickup_signed_at']

                pickup_leg.save(update_fields=update_fields)
                # Horodatage collecte client
                Order.objects.filter(pk=order.pk).update(pickup_time=now)
                sync_order_status_from_legs(order, save=True)
        except Exception:
            import logging
            logging.getLogger("fagni.driver_api").exception("Echec silencieux: pickup_leg save + Order.pickup_time + sync_order_status_from_legs | order_id=%s", getattr(order, "id", None))

        # Event logging LOT1
        try:
            from orders.models import log_event
            log_event(
                "pickup.collected", order=order,
                actor_type="driver", actor_id=driver.id,
                articles_count=articles_count,
            )
        except Exception:
            import logging
            logging.getLogger("fagni.driver_api").exception("Echec silencieux: pickup.collected (log_event) | order_id=%s", getattr(order, "id", None))

        # Payout différé : aucun crédit wallet à la collecte.
        # Crédit déclenché uniquement après order.done + payment_status=paid.
        # wave_link stocké dans notes pour compatibilité

        return Response({
            'success':        True,
            'wa_client':      wa_link,
            'message':        f'Collecte confirmée — {articles_count} articles',
            'total': float(getattr(order, 'total_client_ttc', 0) or 0),
            'articles_count': articles_count,
        'payment_status': order.payment_status,
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
        from orders.models import DeliveryLeg as _DL
        if not _DL.objects.filter(order=order, leg_type="pickup", status="done").exists():
            return Response({"error": "Collecte non confirmee dabord"}, status=400)

        client_name = (request.data.get('client_name') or '').strip()
        photo_b64   = (request.data.get('photo') or '').strip()
        lat = request.data.get('lat')
        lng = request.data.get('lng')

        if not client_name:
            return Response({'error': 'Prenom/nom du client requis'}, status=400)
        if not photo_b64:
            pass  # Photo optionnelle en pilote

        now = timezone.now()

        if photo_b64:
            try:
                from orders.photo_api import _safe_decode_image
                from django.core.files.base import ContentFile
                raw, ext = _safe_decode_image(photo_b64)
                evidence = OrderEvidencePhoto(
                    order=order, leg=leg,
                    actor_type='driver', actor_id=driver.id,
                    kind='delivery_to_client',
                    caption=f'Remis a : {client_name}',
                )
                evidence.image.save(
                    f'delivery_{order.id}_{int(now.timestamp())}.{ext}',
                    ContentFile(raw),
                    save=True,
                )
            except Exception:
                import logging
                logging.getLogger("fagni.driver_api").exception("Echec silencieux: delivery_to_client evidence.image.save | order_id=%s", getattr(order, "id", None))

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
            import logging
            logging.getLogger("fagni.orders.driver_api").exception("Exception silencieuse (auto-log) - fichier=orders/driver_api.py ligne=433")

        update_fields = ['client_signature', 'client_signed_at', 'driver', 'status']
        if hasattr(leg, 'finished_at'): update_fields.append('finished_at')
        if hasattr(leg, 'delivered_lat'): update_fields += ['delivered_lat', 'delivered_lng']
        leg.save(update_fields=update_fields)

        from orders.models import sync_order_status_from_legs
        Order.objects.filter(pk=order.pk).update(delivered_time=now)
        sync_order_status_from_legs(order, save=True)

        # Payout différé : aucun crédit wallet à la livraison.
        # Crédit déclenché uniquement après order.done + payment_status=paid.
        return Response({
            'success': True,
            'message': f'Livraison confirmee — remis a {client_name}',
            'client_name': client_name,
            'delivered_time': now.isoformat(),
        })
    except Exception as e:
        import traceback
        return Response({'error': str(e), 'traceback': traceback.format_exc()}, status=400)



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

        # 🔒 Garde-fou métier : impossible de déposer au pressing
        # si aucun pressing n'est assigné à la commande.
        if not getattr(order, 'laundry_partner_id', None):
            return Response({
                'error': 'pressing_non_assigne',
                'message': 'Impossible de déposer : aucun pressing n’est assigné à cette commande.'
            }, status=400)

        now = timezone.now()
        photo_b64 = (request.data.get('photo') or '').strip()

        # C5 — dropoff_time obligatoire
        Order.objects.filter(pk=order.pk).update(dropoff_time=now)

        # Photo dépôt pressing (obligatoire)
        if photo_b64:
            try:
                from orders.photo_api import _safe_decode_image
                from django.core.files.base import ContentFile
                raw, ext = _safe_decode_image(photo_b64)
                evidence = OrderEvidencePhoto(
                    order=order,
                    actor_type='driver', actor_id=driver.id,
                    kind='dropoff_to_laundry',
                    caption=f'Depose par {driver.name}',
                )
                evidence.image.save(
                    f'dropoff_{order.id}_{int(now.timestamp())}.{ext}',
                    ContentFile(raw),
                    save=True,
                )
            except Exception:
                import logging
                logging.getLogger("fagni.driver_api").exception("Echec silencieux: dropoff_to_laundry evidence.image.save | order_id=%s", getattr(order, "id", None))

        # 🔒 Garde-fou métier : seul le livreur reellement affecte a la
        # jambe pickup peut la finaliser. On ne réaffecte jamais
        # silencieusement pickup_leg.driver au livreur appelant.
        pickup_leg = DeliveryLeg.objects.filter(
            order=order, leg_type='pickup'
        ).first()
        if not pickup_leg or not pickup_leg.driver_id or pickup_leg.driver_id != driver.id:
            return Response({
                'error': 'mission_non_affectee',
                'message': 'Cette mission de collecte n’est pas affectée à ce livreur.',
            }, status=403)

        if pickup_leg.status != 'done':
            pickup_leg.status = 'done'
            if hasattr(pickup_leg, 'finished_at'):
                pickup_leg.finished_at = now
            pickup_leg.save(update_fields=['status'] + (['finished_at'] if hasattr(pickup_leg, 'finished_at') else []))

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
    except Exception:
        import logging
        logging.getLogger("fagni.driver_api").exception(
            "Echec api_driver_dropoff | order_id=%s", order_id,
        )
        return Response({'error': 'Erreur lors du depot au pressing'}, status=400)



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

            if amount >= 125:
                disponible = amount - 100
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
            'direction': getattr(tx, 'direction', 'in'),
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
            .filter(driver=driver, status__in=['assigned', 'in_progress'])
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
        driver = DeliveryPartner.objects.get(id=payload['did'])
        wave = request.data.get('wave_number','').strip()
        if wave:
            driver.wave_number = wave
            driver.save(update_fields=['wave_number'])
        return Response({'success': True, 'wave_number': driver.wave_number})
    except Exception as e:
        return Response({'error': str(e)}, status=400)

@api_view(['POST'])
@permission_classes([AllowAny])
def save_fcm_token(request):
    try:
        token = request.data.get('token')
        user_type = request.data.get('user_type', 'driver')
        user_id = request.data.get('user_id')
        if not token:
            return Response({'error': 'token requis'}, status=400)

        # Verifier que l'utilisateur authentifie correspond au user_id/user_type fournis
        auth_token = request.headers.get('Authorization', '').replace('Bearer ', '')
        try:
            payload = jwt.decode(auth_token, settings.SECRET_KEY, algorithms=['HS256'])
        except Exception:
            return Response({'error': 'Non autorise'}, status=401)
        auth_id = None
        auth_type = None
        if 'cid' in payload:
            auth_type, auth_id = 'client', payload['cid']
        elif 'did' in payload:
            auth_type, auth_id = 'driver', payload['did']
        elif 'pid' in payload:
            auth_type, auth_id = 'partner', payload['pid']
        elif payload.get('ops') is True:
            # Token OPS ({'ops': True, 'name': ...}, ops_login) n'a pas
            # d'id individuel comme les autres profils - un seul type
            # 'ops' partage par toute l'equipe OPS (cf. user_id=1 fixe
            # envoye par fagni-ops/Login.jsx). Jamais reconnu jusqu'ici,
            # cause du 401 systematique sur /api/fcm/token/ pour OPS,
            # decouvert le 19 juillet lors du debug notification simulateur.
            auth_type, auth_id = 'ops', user_id
        if auth_type is None or auth_type != user_type or str(auth_id) != str(user_id):
            return Response({'error': 'Non autorise'}, status=401)
        from orders.models import FCMToken

        # Un même appareil/token ne doit pas être attaché à plusieurs profils.
        # Sinon Firebase reçoit plusieurs envois vers le même téléphone.
        FCMToken.objects.filter(token=token).exclude(
            user_type=user_type,
            user_id=user_id
        ).delete()

        FCMToken.objects.update_or_create(
            user_type=user_type, user_id=user_id,
            defaults={'token': token}
        )
        return Response({'ok': True})
    except Exception as e:
        return Response({'error': str(e)}, status=500)

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_update_location(request):
    """POST /api/driver/location/ — {lat, lng} mise a jour position GPS livreur"""
    try:
        driver = _get_driver(request)
    except Exception:
        return Response({'error': 'Non autorise'}, status=401)
    try:
        lat = request.data.get('lat')
        lng = request.data.get('lng')
        if lat is None or lng is None:
            return Response({'error': 'lat et lng requis'}, status=400)
        from partners.models import DeliveryPartner
        DeliveryPartner.objects.filter(pk=driver.pk).update(
            latitude=float(lat),
            longitude=float(lng),
        )
        return Response({'ok': True})
    except Exception as e:
        return Response({'error': str(e)}, status=400)
