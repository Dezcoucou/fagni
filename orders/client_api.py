import os
import jwt
from decimal import Decimal
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum, F, Value, DecimalField, Q
from django.db.models.functions import Coalesce, Cast

from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Order, Customer, PilotWhitelist

# Sprint P0, Wave 1 (BP2). Message exact valide par le CTO - ne pas
# reformuler, le frontend l'affiche tel quel (voir Login.jsx).
PILOT_WHITELIST_MESSAGE = (
    "Le pilote FAGNI est actuellement accessible uniquement sur invitation. "
    "Contactez notre équipe si vous souhaitez participer."
)


# ── JWT CUSTOM CLIENT ─────────────────────────────────────

def _make_token(customer):
    payload = {
        'cid':   customer.id,
        'phone': customer.phone,
        'exp':   datetime.utcnow() + timedelta(days=30),
        'iat':   datetime.utcnow(),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

def _read_token(token):
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    except Exception:
        return None

class ClientAuth(BaseAuthentication):
    def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            return None
        payload = _read_token(header[7:])
        if not payload:
            raise AuthenticationFailed('Token invalide')
        customer = Customer.objects.filter(id=payload.get('cid')).first()
        if not customer:
            raise AuthenticationFailed('Client introuvable')
        return (customer, header[7:])


# ── HELPERS ───────────────────────────────────────────────

def _items_sum_annotation():
    return Coalesce(
        Sum(
            Cast(F("items__quantity"), DecimalField(max_digits=10, decimal_places=2))
            * Cast(F("items__unit_price"), DecimalField(max_digits=10, decimal_places=2)),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
        Value(Decimal("0"), output_field=DecimalField(max_digits=12, decimal_places=2)),
    )

def _order_to_dict(o):
    total = float(o.total_client_ttc or 0) or float(getattr(o, 'total', 0) or 0) or float(getattr(o, 'items_total', 0) or 0)
    total = max(0, total - float(getattr(o, 'coupon_discount_applied', 0) or 0))
    return {
        'id':             o.id,
        'code':           o.code or str(o.id),
        'status':         o.status,
        'payment_status': getattr(o, 'payment_status', 'unpaid'),
        'service_type':   getattr(o, 'service_type', None) or 'FAGNI',
        'total':          total,
        'created_at':     o.created_at.isoformat() if o.created_at else None,
    }

def _wallet_balance(customer):
    try:
        from wallets.models import Wallet
        w = Wallet.objects.filter(customer=customer).first()
        return float(w.balance or 0) if w else 0.0
    except Exception:
        return 0.0


# ── ENDPOINTS ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def api_login(request):
    """POST /api/client/auth/login/ — { phone } → { access, customer }"""
    phone = (request.data.get('phone') or '').strip()
    if not phone:
        return Response({'error': 'Numéro requis'}, status=400)
    if settings.PILOT_WHITELIST_ENFORCED and not PilotWhitelist.is_authorized(phone):
        return Response({'error': PILOT_WHITELIST_MESSAGE}, status=403)
    customer = Customer.objects.filter(phone=phone).first()
    if not customer:
        return Response({'error': 'Numéro non reconnu'}, status=404)
    return Response({
        'access': _make_token(customer),
        'customer': {'id': customer.id, 'name': customer.name, 'phone': customer.phone},
    })



@api_view(['POST'])
@permission_classes([AllowAny])
def api_register(request):
    """POST /api/client/auth/register/ — {phone, name, referral_code}"""
    phone = (request.data.get('phone') or '').strip()
    name  = (request.data.get('name') or '').strip()
    referral_code = (request.data.get('referral_code') or '').strip()

    if not phone:
        return Response({'error': 'Numéro requis'}, status=400)
    if not name:
        return Response({'error': 'Nom requis'}, status=400)
    if settings.PILOT_WHITELIST_ENFORCED and not PilotWhitelist.is_authorized(phone):
        return Response({'error': PILOT_WHITELIST_MESSAGE}, status=403)

    # Vérifier si client existe déjà
    existing = Customer.objects.filter(phone=phone).first()
    if existing:
        return Response({
            'access': _make_token(existing),
            'customer': {'id': existing.id, 'name': existing.name, 'phone': existing.phone},
            'created': False
        })

    # Créer le client
    customer = Customer.objects.create(name=name, phone=phone)

    # Créer son ReferralLink
    try:
        from mlm.models import ReferralLink
        sponsor = None
        if referral_code:
            sponsor = ReferralLink.objects.filter(referral_code=referral_code).first()

        # Générer un code unique pour ce client
        import uuid
        code = f'FAGNI-{customer.id}-{uuid.uuid4().hex[:4].upper()}'
        ReferralLink.objects.create(
            customer=customer,
            referral_code=code,
            sponsor=sponsor,
            actor_type='customer'
        )
    except Exception as e:
        pass  # Ne pas bloquer l'inscription si MLM échoue

    return Response({
        'access': _make_token(customer),
        'customer': {'name': customer.name, 'phone': customer.phone},
        'created': True
    }, status=201)

@api_view(['GET'])
@authentication_classes([ClientAuth])
@permission_classes([])
def api_home(request):
    """GET /api/client/home/ — données homepage"""
    customer = request.user
    qs = (
        Order.objects.filter(customer=customer)
        .order_by("-created_at")
        .annotate(items_total=_items_sum_annotation())
        .filter(Q(total_client_ttc__gt=0) | Q(items_total__gt=0))
    )
    # Annonce active (Pilot Growth Plan, 10 juillet 2026)
    announcement_data = None
    try:
        from orders.models import Announcement
        ann = Announcement.objects.filter(is_active=True).order_by('-created_at').first()
        if ann and ann.is_currently_valid():
            announcement_data = {
                'title': ann.title,
                'message': ann.message,
                'emoji': ann.emoji,
                'image': ann.image.url if ann.image else None,
            }
    except Exception:
        pass

    return Response({
        'announcement': announcement_data,
        'customer': {
            'name':    customer.name,
            'phone':   customer.phone,
            'initial': (customer.name or 'F')[0].upper(),
            'is_founding_client_eligible': customer.is_founding_client_eligible(),
            'founding_badge_unlocked': customer.founding_badge_unlocked(),
        },
        'wallet_balance':      _wallet_balance(customer),
        'orders_count_all':    qs.count(),
        'active_orders_count': qs.filter(status__in=['pending', 'in_progress']).count(),
        'unpaid_orders_count': qs.exclude(payment_status='paid').count(),
        'recent_orders':       [_order_to_dict(o) for o in qs[:5]],
    })


@api_view(['GET'])
@authentication_classes([ClientAuth])
@permission_classes([])
def api_orders(request):
    """GET /api/client/orders/?page=1"""
    customer  = request.user
    page      = max(1, int(request.GET.get('page', 1)))
    per_page  = 10
    qs = (
        Order.objects.filter(customer=customer)
        .order_by("-created_at")
        .annotate(items_total=_items_sum_annotation())
        .filter(Q(total_client_ttc__gt=0) | Q(items_total__gt=0))
    )
    total  = qs.count()
    start  = (page - 1) * per_page
    return Response({
        'count':   total,
        'page':    page,
        'pages':   max(1, (total + per_page - 1) // per_page),
        'results': [_order_to_dict(o) for o in qs[start:start + per_page]],
    })


# Sprint P0, Wave 2 (BP1) — fenêtre de réutilisation d'une session Checkout
# Wave déjà créée pour une commande, avant d'en générer une nouvelle. L'API
# de création de session Wave consommée ici ne renvoie pas de champ
# d'expiration : cette fenêtre est une politique de renouvellement interne
# volontairement courte, pas une garantie de validité côté Wave. Passé ce
# délai, une nouvelle session est créée et remplace la précédente.
WAVE_CHECKOUT_SESSION_TTL_SECONDS = 25 * 60


def _get_or_create_wave_checkout(order, amount_xof, request):
    """
    Sprint P0, Wave 2 (BP1). Retourne (checkout_url, checkout_id) pour la
    commande, ou (None, None) si WAVE_CHECKOUT_ENABLED est désactivé, si le
    montant est nul, ou si l'appel à l'API Wave échoue/timeout — dans tous
    ces cas l'appelant doit retomber sur le lien marchand statique existant,
    jamais bloquer le client.

    Ne réutilise/écrit jamais `payment_declared_reference` (déjà utilisé pour
    3 usages distincts : référence tapée par le client, référence libre
    saisie par OPS via ops_mark_paid, ancien id de session Checkout de
    client_order_pay_wave_page) : l'id de session est stocké dans le champ
    dédié `wave_checkout_id`/`wave_checkout_url` ajouté pour cette Wave.

    Concurrence : la lecture + création est faite sous select_for_update sur
    la commande, pour qu'un deuxième appel simultané (ou un deuxième GET
    successif) voie la session fraîchement créée par le premier plutôt que
    d'en créer une seconde.
    """
    api_enabled = bool(getattr(settings, "WAVE_CHECKOUT_ENABLED", False))
    api_key = (getattr(settings, "WAVE_CHECKOUT_API_KEY", "") or "").strip()

    if not api_enabled or not api_key or amount_xof <= 0:
        return None, None

    import json
    import time
    import hmac
    import hashlib
    import urllib.request
    import urllib.error
    from django.utils import timezone

    # Toute la section ci-dessous (verrouillage, appel réseau Wave, écriture
    # DB) est volontairement dans un seul try/except large : une panne à
    # n'importe quelle étape (Wave indisponible, DB verrouillée sous forte
    # concurrence, etc.) doit produire (None, None) et jamais remonter une
    # exception jusqu'à api_order_detail - le client ne doit jamais recevoir
    # une 500 à cause d'un problème de session Checkout.
    try:
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)

            existing_id = (locked_order.wave_checkout_id or "").strip()
            existing_url = (locked_order.wave_checkout_url or "").strip()
            created_at = locked_order.wave_checkout_created_at

            if existing_id and existing_url and created_at:
                age_seconds = (timezone.now() - created_at).total_seconds()
                if age_seconds < WAVE_CHECKOUT_SESSION_TTL_SECONDS:
                    return existing_url, existing_id

            base = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
            if not base:
                base = request.build_absolute_uri("/").rstrip("/")

            payload = {
                "amount": str(int(amount_xof)),
                "currency": "XOF",
                "success_url": base + f"/api/client/orders/{order.id}/?wave=success",
                "error_url": base + f"/api/client/orders/{order.id}/?wave=error",
                "client_reference": f"order-{order.id}-{getattr(order, 'code', order.id)}",
            }
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            signing_secret = (getattr(settings, "WAVE_CHECKOUT_SIGNING_SECRET", "") or "").strip()
            if signing_secret:
                timestamp = str(int(time.time()))
                signature = hmac.new(
                    signing_secret.encode("utf-8"),
                    timestamp.encode("utf-8") + body,
                    hashlib.sha256,
                ).hexdigest()
                headers["Wave-Signature"] = f"t={timestamp},v1={signature}"

            req = urllib.request.Request(
                "https://api.wave.com/v1/checkout/sessions",
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            checkout_url = (data.get("wave_launch_url") or "").strip()
            checkout_id = (data.get("id") or "").strip()

            if not checkout_url or not checkout_id:
                return None, None

            locked_order.wave_checkout_id = checkout_id
            locked_order.wave_checkout_url = checkout_url
            locked_order.wave_checkout_created_at = timezone.now()
            locked_order.save(update_fields=[
                "wave_checkout_id", "wave_checkout_url", "wave_checkout_created_at",
            ])

            return checkout_url, checkout_id
    except Exception:
        return None, None


@api_view(['GET'])
@authentication_classes([ClientAuth])
@permission_classes([])
def api_order_detail(request, order_id):
    """GET /api/client/orders/<id>/"""
    customer = request.user
    order = Order.objects.filter(id=order_id, customer=customer).annotate(
        items_total=_items_sum_annotation()
    ).first()

    if not order:
        return Response({'error': 'Commande introuvable'}, status=404)

    # Calculer le vrai total depuis pricing engine si total_client_ttc manque
    _total_raw = float(order.total_client_ttc or 0) or float(getattr(order,'total',0) or 0)
    _coupon_discount = float(getattr(order, 'coupon_discount_applied', 0) or 0)
    total = max(0, _total_raw - _coupon_discount)
    amount_paid = float(getattr(order, 'amount_paid', 0) or 0)

    items = []
    for item in order.items.all():
        items.append({
            'id':         item.id,
            'name':       getattr(item, 'name', None) or getattr(item, 'description', None) or 'Article',
            'quantity':   float(getattr(item, 'quantity', 1) or 1),
            'unit_price': float(getattr(item, 'unit_price', 0) or 0),
        })

    # Lien Wave paiement
    import urllib.parse
    wave_number_fagni = "0748643892"
    remaining = max(0, total - amount_paid)
    wave_msg = f"Paiement FAGNI commande {order.code} — {int(remaining):,} FCFA"
    # Lien Wave CI — format standard pour paiement marchand
    wave_number = "0748643892"
    wave_link = f"https://pay.wave.com/m/M_ci_8SO-R9nJg71k/c/ci/?amount={int(remaining)}" if remaining > 0 else None

    # Sprint P0, Wave 2 (BP1) — si activé, remplace le lien marchand statique
    # par une vraie session Checkout Wave (rattachable par le webhook). Toute
    # indisponibilité (flag off, échec, timeout) laisse wave_link inchangé.
    # Jamais de session pour une commande annulée, même si elle n'a jamais
    # été payée (remaining > 0) : rien à encaisser sur une commande annulée.
    if remaining > 0 and order.status != "canceled":
        checkout_url, _checkout_id = _get_or_create_wave_checkout(order, int(remaining), request)
        if checkout_url:
            wave_link = checkout_url

    # Statut livraison détaillé
    status_map = {
        'pending':     {'label': 'En attente', 'emoji': '⏳', 'step': 1},
        'assigned':    {'label': 'Livreur assigné', 'emoji': '🛵', 'step': 2},
        'in_progress': {'label': 'Collecte en cours', 'emoji': '📦', 'step': 3},
        'at_pressing': {'label': 'Au pressing', 'emoji': '🧺', 'step': 4},
        'ready':       {'label': 'Prêt pour livraison', 'emoji': '✅', 'step': 5},
        'delivering':  {'label': 'En livraison', 'emoji': '🚀', 'step': 6},
        'done':        {'label': 'Livré', 'emoji': '🎉', 'step': 7},
        'canceled':    {'label': 'Annulé', 'emoji': '❌', 'step': 0},
    }
    status_info = status_map.get(order.status, {'label': order.status, 'emoji': '📋', 'step': 0})

    # Vérifier si déjà noté
    try:
        from orders.models import OrderRating
        already_rated = OrderRating.objects.filter(order=order).exists()
    except:
        already_rated = False

    return Response({
        'id':             order.id,
        'code':           order.code or str(order.id),
        'status':         order.status,
        'status_label':   status_info['label'],
        'status_emoji':   status_info['emoji'],
        'status_step':    status_info['step'],
        'payment_status': getattr(order, 'payment_status', 'unpaid'),
        'service_type':   getattr(order, 'service_type', None) or 'FAGNI',
        'total':          total,
        'amount_paid':    amount_paid,
        'remaining':      remaining,
        'wave_link':      wave_link,
        'can_rate':       order.status == 'done' and not already_rated,
        'already_rated':  already_rated,
        'created_at':     order.created_at.isoformat() if order.created_at else None,
        'items':          items,
    })






def is_in_delivery_zone(lat, lng, address=""):
    """Zone pilote FAGNI — Corridor Riviera (Riviera 2, 3, 4, Ciad)."""
    address = (address or "").lower()
    # Mots-clés corridor Riviera
    zone_ok = [
        "riviera 2", "riviera2", "riviéra 2", "riviéra2",
        "riviera 3", "riviera3", "riviéra 3", "riviéra3",
        "riviera 4", "riviera4", "riviéra 4", "riviéra4",
        "riviera ciad", "riviéra ciad",
        "riviera", "riviéra",
    ]
    # Mots-clés test depuis France
    test_ok = ["roussillon", "vaucluse", "france"]
    if any(word in address for word in test_ok):
        return True
    if any(word in address for word in zone_ok):
        return True
    # Coordonnees GPS corridor Riviera
    if lat and lng:
        try:
            lat = float(lat); lng = float(lng)
            return 5.310 <= lat <= 5.410 and -3.985 <= lng <= -3.920
        except:
            import logging
            logging.getLogger("fagni.orders.client_api").exception("Exception silencieuse (auto-log) - fichier=orders/client_api.py ligne=304")
    return False


def _bc1_auto_assign_pickup_and_laundry(order):
    """
    Sprint P0, Wave 3 (BC1) — auto-affectation additive a la creation d'une
    commande fagni-client, derriere settings.AUTO_ASSIGN_ON_CLIENT_ORDER
    (defaut False = jamais appelee).

    Affecte uniquement :
    - laundry_partner (pressing) ;
    - pickup_driver (livreur de collecte).
    Ne touche JAMAIS delivery_partner (livreur retour) : ce role reste
    gere apres le statut pressing "pret" par le flux OPS existant
    (ops_assign_return_driver) - regle metier confirmee par l'audit
    prealable (Wave 3, section "Analyse des effets").

    Reutilise integralement orders.assignment (pick_best_laundry/
    pick_best_driver) et orders.services.recompute_order_pricing_for_laundry_partner
    (meme fonction que ops_assign_partner) - aucun nouveau moteur, aucune
    nouvelle formule. Reproduit aussi, a l'identique, les quelques lignes
    deja utilisees par ops_assign_driver pour rendre la collecte visible/
    payable au livreur (creation/mise a jour de la DeliveryLeg pickup +
    driver_amount + order.cost_driver_pickup) : sans cela, l'affectation
    ne serait pas exploitable par l'app livreur (driver_missions/
    driver_pending_mission filtrent sur DeliveryLeg.driver, jamais sur
    Order.pickup_driver_id directement).

    Ne leve jamais d'exception : chaque etape (pressing, livreur) est
    isolee dans son propre try/except, pour qu'un echec sur l'un ne
    bloque jamais l'autre ni la creation de la commande.
    """
    from orders.assignment import pick_best_laundry, pick_best_driver
    from orders.services import recompute_order_pricing_for_laundry_partner
    from orders.models import DeliveryLeg
    from orders.ops_api import _send_notif_pressing, _send_notif_mission

    result = {"laundry_assigned": False, "driver_assigned": False, "pricing_recomputed": False}

    # Garde-fou (defense en profondeur, meme si l'appel actuel est toujours
    # synchrone juste apres la creation donc order.status vaut deja "pending") :
    # une commande annulee ne doit jamais recevoir d'affectation.
    if getattr(order, "status", None) == "canceled":
        return result

    try:
        laundry, _reason = pick_best_laundry(order)
        if laundry:
            order.laundry_partner = laundry
            update_fields = ["laundry_partner"]

            recomputed = False
            try:
                recomputed = recompute_order_pricing_for_laundry_partner(order, laundry)
            except Exception:
                recomputed = False

            if recomputed:
                update_fields += [
                    "delivery_fee", "distance_km_pickup", "distance_km_delivery",
                    "distance_km_total", "distance_km",
                    "total_client_ttc", "total", "service_fee",
                    "amount_laundry_partner", "fagni_revenue_ht", "margin_net",
                ]

            order.save(update_fields=update_fields)
            result["laundry_assigned"] = True
            result["pricing_recomputed"] = recomputed
    except Exception:
        import logging
        logging.getLogger("fagni.orders.client_api").exception(
            "BC1 auto-affectation pressing en echec | order_id=%s", getattr(order, "id", None)
        )

    try:
        driver, _reason = pick_best_driver(order)
        if driver:
            from orders.config_models import GlobalPricingSettings
            from decimal import Decimal as _D

            driver_amount = _D(str(GlobalPricingSettings.get_solo().driver_amount_per_leg))

            order.pickup_driver = driver
            order.cost_driver_pickup = int(driver_amount)
            order.save(update_fields=["pickup_driver", "cost_driver_pickup"])

            leg, created = DeliveryLeg.objects.get_or_create(
                order=order,
                leg_type="pickup",
                defaults={"driver": driver, "status": "pending", "driver_amount": driver_amount},
            )
            if not created:
                leg.driver = driver
            if leg.status == "pending":
                leg.status = "assigned"
            leg.driver_amount = driver_amount
            leg.save(update_fields=["driver", "status", "driver_amount"])

            result["driver_assigned"] = True
    except Exception:
        import logging
        logging.getLogger("fagni.orders.client_api").exception(
            "BC1 auto-affectation livreur collecte en echec | order_id=%s", getattr(order, "id", None)
        )

    # Notifications : _send_notif_pressing/_send_notif_mission neutralisent
    # deja leurs propres exceptions en interne (voir orders/ops_api.py), mais
    # on garde un filet ici aussi - l'affectation ci-dessus est deja
    # sauvegardee, une panne de notification ne doit jamais faire remonter
    # d'exception hors de cette fonction.
    try:
        if result["laundry_assigned"]:
            _send_notif_pressing(order)
        if result["driver_assigned"]:
            _send_notif_mission(order, order.pickup_driver)
    except Exception:
        import logging
        logging.getLogger("fagni.orders.client_api").exception(
            "BC1 notification en echec | order_id=%s", getattr(order, "id", None)
        )

    return result


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def api_create_order(request):
    """POST /api/client/orders/create/"""
    from datetime import date, time as dtime, timedelta

    # Authentification manuelle
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from orders.models import Customer
        customer = Customer.objects.get(id=payload['cid'])
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    accepted_cgu = str(
        request.data.get('accepted_cgu') or request.data.get('cgu_accepted') or ''
    ).strip().lower()

    if accepted_cgu not in ('1', 'true', 'yes', 'on'):
        return Response({
            'error': 'cgu_required',
            'message': "Merci d'accepter les Conditions Générales d'Utilisation FAGNI avant de confirmer la commande.",
            'cgu_version': '1.4',
        }, status=400)

    bag_size      = (request.data.get('bag_size') or '').strip()
    pickup_addr   = (request.data.get('pickup_address') or '').strip()
    pickup_lat    = request.data.get('pickup_lat')
    pickup_lng    = request.data.get('pickup_lng')

    # Vérification zone de livraison
    if not is_in_delivery_zone(pickup_lat, pickup_lng, pickup_addr):
        return Response({
            'error': 'zone_unavailable',
            'message': 'Désolé, FAGNI nest pas encore disponible dans votre zone. Nous arrivons bientot a Riviera 3 !',
            'zones_disponibles': ['Riviera 3', 'Cocody', 'Riviera']
        }, status=400)
    pickup_slot   = (request.data.get('pickup_slot') or '').strip()
    articles      = request.data.get('articles', [])



    today    = date.today()

    slot_map = {
        'demain_matin':       (today + timedelta(days=1), dtime(8, 0)),
        'demain_soir':        (today + timedelta(days=1), dtime(14, 0)),
        'apres_demain_matin': (today + timedelta(days=2), dtime(8, 0)),
        'apres_demain_soir':  (today + timedelta(days=2), dtime(14, 0)),
    }
    pickup_date, pickup_time = slot_map.get(
        pickup_slot, (today + timedelta(days=1), dtime(8, 0))
    )
    delivery_date = pickup_date + timedelta(days=2)

    # Pricing v3.0 — source de vérité
    from orders.config_models import GlobalPricingSettings
    from decimal import Decimal as _D
    _cfg = GlobalPricingSettings.get_solo()
    _pa = _D(str(_cfg.prix_article_fcfa or 500))
    _df = _D(str(_cfg.delivery_fixed_fee or 2000))
    _cp = _D(str(_cfg.laundry_commission_percent or 40)) / 100


    # Compter les articles réels déclarés par le client
    nb_articles = 0
    if articles:
        for a in articles:
            nb_articles += int(a.get('quantity', 1))
    if nb_articles == 0:
        nb_articles = 5

    _ta = _pa * nb_articles
    _sf_raw = _ta * _D('0.05')  # 5% du montant pressing
    _sf = max(_D('500'), _sf_raw)  # minimum 500 FCFA
    _total = _ta + _df + _sf
    _pricing = {'total_client':float(_total),'delivery_fee':float(_df),'service_fee':float(_sf),'part_pressing':float(_ta*(1-_cp)),'part_livreur':float(_df*_D('0.80')),'marge_pressing':float(_ta*_cp),'marge_livraison':float(_df*_D('0.20')),'total_fagni':float(_ta*_cp+_sf+_df*_D('0.20'))}
    bag_price = float(_ta + _df + _sf)
    service_fee = _sf
    total       = _pricing['total_client']

    notes_parts = []
    if articles:
        notes_parts.append(f"Articles: {articles}")
    if pickup_slot:
        notes_parts.append(f"Creneau: {pickup_slot}")

    try:
        # Calcul financier — déjà fait via PricingConfig
        pricing = _pricing

        # Champs de base — toujours présents
        order_data = {
            'customer':                customer,
            'bag_size':                bag_size,
            'pickup_address':          pickup_addr or getattr(customer, 'address', '') or '',
            'pickup_lat':              pickup_lat,
            'pickup_lng':              pickup_lng,
            'delivery_address':        pickup_addr or getattr(customer, 'address', '') or '',
            'delivery_lat': pickup_lat or getattr(customer, 'latitude', None),
            'delivery_lng': pickup_lng or getattr(customer, 'longitude', None),
            'pickup_scheduled_date':   pickup_date,
            'pickup_scheduled_time':   pickup_time,
            'delivery_scheduled_date': delivery_date,
            'delivery_scheduled_time': dtime(12, 0),
            'service_fee':             service_fee,
            'notes':                   ' | '.join(notes_parts),
            'status':                  'pending',
            'pricing_mode':            'item',
        }

        # Champs financiers — ajoutés si le champ existe dans le modèle
        financial_fields = {
            'delivery_fee':           pricing['delivery_fee'],
            'commission_delivery_ht': pricing['marge_livraison'],
            'commission_laundry_ht':  pricing['marge_pressing'],
            'amount_driver_partner':  pricing['part_livreur'],
            'amount_laundry_partner': pricing['part_pressing'],
            'fagni_revenue_ht':       pricing['total_fagni'],
            'total_client_ttc':       pricing['total_client'],
        }

        for field, value in financial_fields.items():
            if hasattr(Order, field):
                order_data[field] = value

        # Champs rentabilité LOT1
        pricing_part_livreur = int(_pricing.get('part_livreur', 0))
        order_data.update({
            'cost_driver_pickup':   pricing_part_livreur // 2,
            'cost_driver_delivery': pricing_part_livreur - (pricing_part_livreur // 2),
            'cost_pressing':        int(_pricing.get('part_pressing', 0)),
            'margin_net':           int(_pricing.get('total_fagni', 0)),
        })

        # Score rentabilité
        total_c = int(_pricing.get('total_client', 1)) or 1
        margin  = order_data['margin_net']
        score   = round(margin / total_c * 100, 2)
        order_data['profitability_score'] = score
        order_data['profitability_label'] = (
            'rentable' if score >= 30 else
            'limite'   if score >= 15 else
            'a_perte'
        )

        order = Order.objects.create(**order_data)

        # Application du coupon (ADR-025, Pilot Growth Plan, 9 juillet 2026)
        coupon_code_input = (request.data.get('coupon_code') or '').strip()
        if coupon_code_input:
            try:
                from orders.views import validate_and_get_coupon_discount
                from orders.models import CouponUsage
                discount, error, coupon = validate_and_get_coupon_discount(order, coupon_code_input)
                if coupon and not error and discount > 0:
                    order.coupon_code = coupon.code
                    order.coupon_discount_applied = discount
                    order.save(update_fields=['coupon_code', 'coupon_discount_applied'])
                    CouponUsage.objects.create(coupon=coupon, customer=customer, order=order, discount_amount=discount)
            except Exception:
                import logging
                logging.getLogger("fagni.client_api").exception("Echec application coupon order_id=%s", order.id)
        try:
            from orders.models import OrderItem
            if articles:
                for a in articles:
                    _qty=int(a.get('quantity',1))
                    _aid=str(a.get('id',''))
                    _des=str(a.get('name',a.get('designation','')))
                    if not _des and _aid:
                        try:
                            from orders.models import ArticleConfig
                            _des = ArticleConfig.objects.get(article_id=_aid).label
                        except: _des = f'Article {_aid}'
                    if not _des: _des = 'Article'
                    OrderItem.objects.create(order=order,designation=_des,quantity=_qty,unit_price=int(_pa),total=_qty*int(_pa))
            elif nb_articles>0:
                OrderItem.objects.create(order=order,designation='Articles pressing',quantity=nb_articles,unit_price=int(_pa),total=nb_articles*int(_pa))
        except Exception: pass

        # Sprint P0, Wave 3 (BC1) — auto-affectation additive, desactivee par
        # defaut. Placee ici (apres les OrderItem, pas juste apres le create())
        # car le recalcul pricing partage avec ops_assign_partner a besoin des
        # quantites d'articles deja en base. Si le pricing a ete recalcule,
        # les variables locales total/service_fee/bag_price sont mises a jour
        # AVANT la suite (event logging, notifications OPS, reponse) pour que
        # tout ce qui suit reflete deja le prix reellement sauvegarde.
        bc1_assignment = {"laundry_assigned": False, "driver_assigned": False, "pricing_recomputed": False}
        if getattr(settings, "AUTO_ASSIGN_ON_CLIENT_ORDER", False):
            try:
                bc1_assignment = _bc1_auto_assign_pickup_and_laundry(order)
            except Exception:
                import logging
                logging.getLogger("fagni.orders.client_api").exception(
                    "BC1 auto-affectation en echec | order_id=%s", order.id
                )

            if bc1_assignment.get("pricing_recomputed"):
                try:
                    order.refresh_from_db()
                    total = float(order.total_client_ttc or order.total or total)
                    service_fee = float(order.service_fee or service_fee)
                    bag_price = total
                except Exception:
                    import logging
                    logging.getLogger("fagni.orders.client_api").exception(
                        "BC1: lecture des valeurs sauvegardees en echec | order_id=%s", order.id
                    )

        # Event logging LOT1
        try:
            from orders.models import log_event
            log_event(
                "cgu.accepted", order=order,
                actor_type="client", actor_id=customer.id,
                cgu_version="1.4",
                source="api_create_order",
            )
            log_event(
                "order.created", order=order,
                actor_type="client", actor_id=customer.id,
                total=int(total), articles=nb_articles,
                bag_size=bag_size, margin_net=margin,
                profitability_label=order_data['profitability_label'],
            )
        except Exception:
            import logging
            logging.getLogger("fagni.orders.client_api").exception("Exception silencieuse (auto-log) - fichier=orders/client_api.py ligne=491")

        # Notification WhatsApp OPS
        try:
            import urllib.request, urllib.parse
            _ops_raw = (GlobalPricingSettings.get_solo().whatsapp_ops_number or "0142299949").lstrip("0")
            ops_num = "225" + _ops_raw if not _ops_raw.startswith("225") else _ops_raw
            msg = f"🆕 Nouvelle commande FAGNI\n\nCode : {order.code}\nClient : {customer.name}\nSac : {bag_size}\nTotal : {total:,} FCFA\nAdresse : {pickup_addr}\n\nConnecte-toi sur fagni-ops.vercel.app"

            # Sprint P0, Wave 3 (BC1) — flag desactive : message strictement
            # inchange. Flag active : etat anonymise ajoute au message OPS
            # existant (aucun nouveau canal) uniquement si l'auto-affectation
            # n'a pas pleinement reussi.
            if getattr(settings, "AUTO_ASSIGN_ON_CLIENT_ORDER", False):
                if not bc1_assignment.get("laundry_assigned"):
                    msg += "\n\n⚠️ Pressing : non affecté"
                elif not bc1_assignment.get("pricing_recomputed"):
                    msg += "\n\n⚠️ Pressing : affecté, prix non recalculé (à vérifier)"
                if not bc1_assignment.get("driver_assigned"):
                    msg += "\n⚠️ Collecte : livreur non affecté"

            wa_url = f"https://wa.me/{ops_num}?text={urllib.parse.quote(msg)}"
            order.notes = (order.notes or '') + f'\nWA_OPS:{wa_url}'
            order.save(update_fields=['notes'])
        except:
            import logging
            logging.getLogger("fagni.orders.client_api").exception("Exception silencieuse (auto-log) - fichier=orders/client_api.py ligne=503")

        # Notification push OPS
        try:
            from orders.models import FCMToken
            from fagni.notifications import send_push

            title = "Nouvelle commande FAGNI"
            body = f"{order.code} - {customer.name} - {total:,} FCFA"
            for t in FCMToken.objects.filter(user_type="ops"):
                send_push(
                    t.token,
                    title,
                    body,
                    {"type": "ops_new_order", "order_id": order.id, "order_code": order.code or str(order.id)}
                )
        except Exception as e:
            print(f"[NOTIF] ops new order: {e}")

        return Response({
            'order_id':      order.id,
            'code':          order.code or str(order.id),
            'bag_size':      bag_size,
            'bag_price':     bag_price,
            'service_fee':   service_fee,
            'total':         total,
            'pickup_date':   pickup_date.strftime('%d/%m/%Y'),
            'pickup_slot':   pickup_slot,
            'delivery_date': delivery_date.strftime('%d/%m/%Y'),
        }, status=201)
    except Exception as e:
        return Response({'error': str(e)}, status=400)




@api_view(['GET'])
@permission_classes([AllowAny])
def api_wallet(request):
    """GET /api/client/wallet/ — solde + historique transactions"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from orders.models import Customer
        customer = Customer.objects.get(id=payload['cid'])
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from wallets.models import Wallet, WalletTransaction
        wallet = Wallet.objects.filter(customer=customer).first()
        balance = float(wallet.balance) if wallet else 0.0

        transactions = []
        if wallet:
            txs = WalletTransaction.objects.filter(
                wallet=wallet
            ).order_by('-created_at')[:50]
            for tx in txs:
                transactions.append({
                    'id':          tx.id,
                    'type':        tx.type,
                    'direction':   tx.direction,
                    'amount':      float(tx.amount),
                    'description': tx.description or '',
                    'created_at':  tx.created_at.isoformat() if tx.created_at else None,
                })

        return Response({
            'balance':      balance,
            'currency':     'FCFA',
            'transactions': transactions,
        })
    except Exception as e:
        return Response({'balance': 0.0, 'currency': 'FCFA', 'transactions': [], 'error': str(e)})


@api_view(['GET'])
@permission_classes([AllowAny])
def api_parrainage(request):
    """GET /api/client/parrainage/ — lien de parrainage + commissions"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from orders.models import Customer
        customer = Customer.objects.get(id=payload['cid'])
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from mlm.models import ReferralLink, ReferralCommission
        from wallets.models import WalletTransaction

        link = ReferralLink.objects.filter(customer=customer).first()
        code = link.referral_code if link else f'FAGNI-{customer.id}'

        commissions = []
        if link:
            txs = WalletTransaction.objects.filter(
                wallet__customer=customer,
                type='mlm_commission'
            ).order_by('-created_at')[:20]
            for tx in txs:
                commissions.append({
                    'amount':      float(tx.amount),
                    'description': tx.description or '',
                    'created_at':  tx.created_at.isoformat() if tx.created_at else None,
                })

        total_gains = sum(c['amount'] for c in commissions)

        return Response({
            'code':         code,
            'lien':         f'https://fagni.app/ref/{code}',
            'total_gains':  total_gains,
            'commissions':  commissions,
            'filleuls':     ReferralLink.objects.filter(sponsor=customer).count() if link else 0,
        })
    except Exception as e:
        return Response({'code': f'FAGNI-{customer.id}', 'total_gains': 0, 'commissions': [], 'filleuls': 0})


@api_view(['POST'])
@permission_classes([AllowAny])
def api_report_litige(request, order_id):
    """POST /api/client/orders/<id>/litige/ — {type, description}"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from orders.models import Customer
        customer = Customer.objects.get(id=payload['cid'])
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from orders.models import Order
        order = Order.objects.get(id=order_id, customer=customer)
        litige_type = request.data.get('type', '').strip()
        description = request.data.get('description', '').strip()

        # Stocker dans les notes de la commande
        notes = order.notes or ''
        tag = f'\nLITIGE:{litige_type}|{description[:200]}'
        order.notes = notes + tag
        order.save(update_fields=['notes', 'updated_at'])

        return Response({'success': True, 'message': 'Litige enregistré'})
    except Exception as e:
        return Response({'error': str(e)}, status=400)

@api_view(['GET'])
@permission_classes([AllowAny])
def api_pricing_detail(request):
    """GET /api/client/pricing/detail/?bag=small&zone=standard"""
    from orders.pricing_engine import calculate_bag_pricing, format_receipt
    bag  = request.GET.get('bag', 'small')
    zone = request.GET.get('zone', 'standard')
    pricing = calculate_bag_pricing(bag, zone)
    pricing['receipt'] = format_receipt(pricing)
    return Response(pricing)

@api_view(['GET'])
@permission_classes([AllowAny])
def api_pricing_bags(request):
    """GET /api/client/pricing/bags/ — prix des sacs v2.0"""
    from orders.pricing_engine import get_bag_pricing, BAG_CONFIG
    pricing = get_bag_pricing()
    return Response(pricing)


@api_view(['GET'])
@permission_classes([AllowAny])
def api_pricing_bags_old(request):
    """GET /api/client/pricing/bags/ — prix depuis la DB"""
    from orders.models import PricingConfig
    from orders.pricing_engine import calculate_order

    configs = PricingConfig.objects.filter(is_active=True).order_by('pressing_amount')
    result = {}
    for config in configs:
        pricing = calculate_order(config.pressing_amount, config.delivery_fee)
        result[config.bag_size] = {
            'label':            config.label or config.get_bag_size_display(),
            'description':      config.description,
            'pressing_amount':  config.pressing_amount,
            'delivery_fee':     config.delivery_fee,
            'service_fee':      pricing['service_fee'],
            'total_client':     pricing['total_client'],
            'total_fagni':      pricing['total_fagni'],
            'part_pressing':    pricing['part_pressing'],
            'part_livreur':     pricing['part_livreur'],
        }
    return Response(result)

@api_view(['GET'])
@permission_classes([AllowAny])
def api_articles(request):
    """GET /api/client/articles/ — catalogue depuis DB + pricing"""
    from orders.models import ArticleConfig, PricingConfig
    from orders.pricing_engine import calculate_order
    from orders.utils.pricing import BAG_PRICING

    # Articles depuis DB
    articles = ArticleConfig.objects.filter(is_active=True).order_by('category','sort_order','label')

    catalog = {}
    for art in articles:
        cat = art.category
        if cat not in catalog:
            catalog[cat] = {'id': cat, 'label': art.get_category_display(), 'items': []}
        catalog[cat]['items'].append({
            'id':         art.article_id,
            'label':      art.label,
            'emoji':      art.emoji,
            'slots':      art.slots,
            'weight_kg':  float(art.weight_kg),
            'max_qty':    art.max_quantity,
            'sur_devis':  art.sur_devis,
            'prix_unitaire': art.prix_unitaire,
        })

    # Bags — Modèle B estimation visuelle
    bags = {
        'small':  {'label':'Petit sac',  'emoji':'🧳','max_items':15,'prix_article_client':500,'delivery_fee':2000,'description':'Idéal pour 1-5 pièces'},
        'medium': {'label':'Sac moyen',  'emoji':'🎒','max_items':25,'prix_article_client':500,'delivery_fee':2000,'description':'Idéal pour 5-15 pièces'},
        'large':  {'label':'Grand sac',  'emoji':'👜','max_items':50,'prix_article_client':500,'delivery_fee':2000,'description':'Famille — 15+ pièces'},
    }
    return Response({
        'bags': bags,
        'catalog': list(catalog.values()),
    })

@api_view(['GET'])
@permission_classes([AllowAny])
def api_order_tracking(request, order_id):
    """GET /api/client/orders/<id>/tracking/ — suivi temps réel"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from orders.models import Customer
        customer = Customer.objects.get(id=payload['cid'])
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from orders.models import Order
        order = Order.objects.get(id=order_id, customer=customer)

        status = order.status
        payment = order.payment_status
        notes   = order.notes or ''

        # DeliveryLegs — source de vérité logistique
        from orders.models import DeliveryLeg, OrderEvidencePhoto

        pickup_leg = DeliveryLeg.objects.filter(order=order, leg_type='pickup').select_related('driver').first()
        return_leg = DeliveryLeg.objects.filter(order=order, leg_type='return').select_related('driver').first()

        pickup_done = bool(pickup_leg and pickup_leg.status == 'done')
        return_done = bool(return_leg and return_leg.status == 'done')
        pickup_active = bool(pickup_leg and pickup_leg.status in ['pending', 'assigned', 'in_progress'])
        return_active = bool(return_leg and return_leg.status in ['pending', 'assigned', 'in_progress'])

        # Construire la timeline
        steps = [
            {
                'id':      'created',
                'label':   'Commande créée',
                'desc':    'Votre commande a été enregistrée',
                'emoji':   '📱',
                'done':    True,
                'active':  status == 'pending',
            },
            {
                'id':      'pickup',
                'label':   'Collecte en cours',
                'desc':    'Le livreur se dirige vers vous',
                'emoji':   '🛵',
                'done':    pickup_done,
                'active':  pickup_active and not pickup_done,
            },
            {
                'id':      'pressing',
                'label':   'Au pressing',
                'desc':    'Vos vêtements sont en cours de traitement',
                'emoji':   '🧺',
                'done':    pickup_done,
                'active':  pickup_done and not return_done,
            },
            {
                'id':      'ready',
                'label':   'Prêt pour livraison',
                'desc':    'Vos vêtements sont propres et prêts',
                'emoji':   '✅',
                'done':    bool(getattr(order, 'wash_complete_time', None)),
                'active':  pickup_done and bool(getattr(order, 'wash_complete_time', None)) and not return_active and not return_done,
            },
            {
                'id':      'delivery',
                'label':   'Livraison en cours',
                'desc':    'Le livreur arrive chez vous',
                'emoji':   '🚗',
                'done':    return_done,
                'active':  return_active and not return_done,
            },
            {
                'id':      'delivered',
                'label':   'Livré',
                'desc':    'Vos vêtements ont été livrés',
                'emoji':   '🎉',
                'done':    return_done,
                'active':  False,
            },
        ]

        # Infos livreur (anonymisé) depuis DeliveryLeg
        pickup_driver = None
        if pickup_leg and pickup_leg.driver:
            pickup_driver = {
                'vehicle': pickup_leg.driver.vehicle_type or 'moto',
                'initials': pickup_leg.driver.name[0].upper() if pickup_leg.driver.name else 'L',
            }

        delivery_driver = None
        if return_leg and return_leg.driver:
            delivery_driver = {
                'vehicle': return_leg.driver.vehicle_type or 'moto',
                'initials': return_leg.driver.name[0].upper() if return_leg.driver.name else 'L',
            }

        # Articles collectés
        articles_count = 0
        # Chercher dans notes format "Articles: [...]"
        import re, ast
        articles_match = re.search(r"Articles: (\[.*?\])", notes.replace("\n"," "))
        if articles_match:
            try:
                arts = ast.literal_eval(articles_match.group(1))
                for a in arts:
                    articles_count += int(a.get('quantity') or a.get('qty') or 1)
            except: pass
        if articles_count == 0:
            for line in notes.split('\n'):
                if line.startswith('COLLECTE:'):
                    try:
                        articles_count = int(line.split(':')[1].split(' ')[0])
                    except: pass

        # ETA selon statut
        from datetime import timedelta
        from django.utils import timezone
        import locale
        try: locale.setlocale(locale.LC_TIME, 'fr_FR.UTF-8')
        except: pass
        now = timezone.now()
        eta_label = None
        eta_desc  = None

        if status == 'pending':
            eta_label = "Collecte prévue"
            pickup_date = getattr(order, 'pickup_scheduled_date', None)
            if pickup_date:
                JOURS = ['Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi','Dimanche']
                MOIS  = ['Jan','Fév','Mar','Avr','Mai','Juin','Juil','Août','Sep','Oct','Nov','Déc']
                eta_desc = f"{JOURS[pickup_date.weekday()]} {pickup_date.day} {MOIS[pickup_date.month-1]}" if hasattr(pickup_date, 'weekday') else str(pickup_date)
            else:
                eta_desc = "Selon votre créneau choisi"
        elif status in ['in_progress', 'assigned']:
            eta_label = "Retour prévu"
            delivery_date = getattr(order, 'delivery_scheduled_date', None)
            if delivery_date:
                JOURS2 = ['Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi','Dimanche']
                MOIS2  = ['Jan','Fév','Mar','Avr','Mai','Juin','Juil','Août','Sep','Oct','Nov','Déc']
                eta_desc = f"{JOURS2[delivery_date.weekday()]} {delivery_date.day} {MOIS2[delivery_date.month-1]}" if hasattr(delivery_date, 'weekday') else str(delivery_date)
            else:
                eta_desc = "Dans 48-72 heures"
        elif status == 'done':
            eta_label = "Livré avec succès"
            eta_desc  = "Merci de nous faire confiance"

        # DeliveryLegs — statuts temps réel

        legs_data = []
        legs = DeliveryLeg.objects.filter(order=order).order_by('created_at')

        JOURS3  = ['Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi','Dimanche']
        MOIS3   = ['Jan','Fév','Mar','Avr','Mai','Juin','Juil','Août','Sep','Oct','Nov','Déc']

        def fmt_dt(dt):
            if not dt: return None
            from django.utils import timezone as tz
            local = dt
            h, m = local.hour, local.minute
            label = "aujourd'hui" if local.date() == now.date() else f"{JOURS3[local.weekday()]} {local.day} {MOIS3[local.month-1]}"
            return f"{label} à {h:02d}h{m:02d}"

        for leg in legs:
            photos = []
            for ep in OrderEvidencePhoto.objects.filter(leg=leg).order_by('created_at'):
                try:
                    url = ep.image.url if ep.image and hasattr(ep.image, 'url') else None
                    # En prod Render : disque éphémère → photos non servies
                    # On garde le metadata mais pas l'URL
                    if url and not url.startswith('/media/'):
                        photos.append({
                            'kind':    ep.kind,
                            'url':     url,
                            'caption': ep.caption or '',
                            'at':      fmt_dt(ep.created_at),
                        })
                except: pass

            legs_data.append({
                'id':         leg.id,
                'type':       leg.leg_type,
                'status':     leg.status,
                'started_at': fmt_dt(leg.started_at),
                'done_at':    fmt_dt(leg.finished_at),
                'photos':     photos,
                'driver':     {
                    'initials': leg.driver.name[0].upper() if leg.driver and leg.driver.name else 'L',
                    'vehicle':  leg.driver.vehicle_type or 'moto' if leg.driver else 'moto',
                } if leg.driver else None,
            })

        # Réassurance
        reassurance = [
            {"icon": "🔒", "text": "Vos vêtements sont sécurisés"},
            {"icon": "📸", "text": "Photos prises à chaque étape"},
            {"icon": "✅", "text": "Pressing partenaire vérifié FAGNI"},
            {"icon": "💰", "text": "Paiement uniquement à la livraison"},
        ]

        # Pressing info
        pressing_info = None
        if order.laundry_partner:
            pressing_info = {
                'initials': order.laundry_partner.name[0].upper() if order.laundry_partner.name else 'P',
            }

        return Response({
            'order_id':        order.id,
            'code':            order.code or str(order.id),
            'status':          status,
            'payment_status':  payment,
            'bag_size':        order.bag_size or '',
        'total': float(max(0, (order.total_client_ttc or order.total or 0) - (order.coupon_discount_applied or 0))),
            'steps':           steps,
            'pickup_driver':   pickup_driver,
            'delivery_driver': delivery_driver,
            'articles_count':  articles_count,
            'has_partner':     order.laundry_partner_id is not None,
            'pressing_info':   pressing_info,
            'created_at':      order.created_at.isoformat() if order.created_at else None,
            'eta_label':       eta_label,
            'eta_desc':        eta_desc,
            'reassurance':     reassurance,
            'legs':            legs_data,
        })
    except Exception as e:
        return Response({'error': str(e)}, status=404)


@api_view(['POST'])
@permission_classes([AllowAny])
def api_cancel_order(request, order_id):
    """POST /api/client/orders/<id>/cancel/ — annuler commande"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from orders.models import Customer
        customer = Customer.objects.get(id=payload['cid'])
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from orders.models import Order
        from django.utils import timezone
        import datetime

        order = Order.objects.get(id=order_id, customer=customer)

        # Vérifier statut
        if order.status in ['done', 'canceled']:
            return Response({'error': 'Cette commande ne peut plus être annulée'}, status=400)

        # Vérifier délai 2h avant collecte
        if order.pickup_scheduled_date and order.pickup_scheduled_time:
            pickup_dt = datetime.datetime.combine(
                order.pickup_scheduled_date,
                order.pickup_scheduled_time
            )
            pickup_dt = timezone.make_aware(pickup_dt)
            now = timezone.now()
            diff = (pickup_dt - now).total_seconds() / 3600

            if diff < 2:
                # Frais annulation tardive
                order.notes = (order.notes or '') + '\nANNULATION_TARDIVE:1000 FCFA'
                order.status = 'canceled'
                order.save(update_fields=['status', 'notes', 'updated_at'])
                return Response({
                    'canceled': True,
                    'late_fee': True,
                    'message': 'Commande annulée. Frais d\'annulation tardive : 1 000 FCFA applicable.'
                })

        order.status = 'canceled'
        order.save(update_fields=['status', 'updated_at'])
        return Response({'canceled': True, 'late_fee': False, 'message': 'Commande annulée sans frais.'})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def api_cancel_order(request, order_id):
    """POST /api/client/orders/<id>/cancel/ — annuler commande"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from orders.models import Customer
        customer = Customer.objects.get(id=payload['cid'])
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from orders.models import Order
        from django.utils import timezone
        import datetime

        order = Order.objects.get(id=order_id, customer=customer)

        # Vérifier statut
        if order.status in ['done', 'canceled']:
            return Response({'error': 'Cette commande ne peut plus être annulée'}, status=400)

        # Vérifier délai 2h avant collecte
        if order.pickup_scheduled_date and order.pickup_scheduled_time:
            pickup_dt = datetime.datetime.combine(
                order.pickup_scheduled_date,
                order.pickup_scheduled_time
            )
            pickup_dt = timezone.make_aware(pickup_dt)
            now = timezone.now()
            diff = (pickup_dt - now).total_seconds() / 3600

            if diff < 2:
                # Frais annulation tardive
                order.notes = (order.notes or '') + '\nANNULATION_TARDIVE:1000 FCFA'
                order.status = 'canceled'
                order.save(update_fields=['status', 'notes', 'updated_at'])
                return Response({
                    'canceled': True,
                    'late_fee': True,
                    'message': 'Commande annulée. Frais d\'annulation tardive : 1 000 FCFA applicable.'
                })

        order.status = 'canceled'
        order.save(update_fields=['status', 'updated_at'])
        return Response({'canceled': True, 'late_fee': False, 'message': 'Commande annulée sans frais.'})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([AllowAny])
def api_wallet(request):
    """GET /api/client/wallet/ — solde + historique + alerte expiration"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from orders.models import Customer
        customer = Customer.objects.get(id=payload['cid'])
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from wallets.models import Wallet, WalletTransaction
        from django.utils import timezone
        import datetime

        wallet = Wallet.objects.filter(customer=customer).first()
        balance = float(wallet.balance) if wallet else 0.0

        # Vérifier expiration — dernière commande
        from orders.models import Order
        last_order = Order.objects.filter(
            customer=customer
        ).order_by('-created_at').first()

        wallet_expires = None
        wallet_warning = False
        if last_order and last_order.created_at:
            expiry_date = last_order.created_at + datetime.timedelta(days=365)
            days_left = (expiry_date - timezone.now()).days
            wallet_expires = expiry_date.strftime('%d/%m/%Y')
            wallet_warning = days_left < 30

        transactions = []
        if wallet:
            txs = WalletTransaction.objects.filter(
                wallet=wallet
            ).order_by('-created_at')[:50]
            for tx in txs:
                transactions.append({
                    'id':          tx.id,
                    'type':        tx.type,
                    'direction':   tx.direction,
                    'amount':      float(tx.amount),
                    'description': tx.description or '',
                    'created_at':  tx.created_at.isoformat() if tx.created_at else None,
                })

        return Response({
            'balance':        balance,
            'currency':       'FCFA',
            'transactions':   transactions,
            'wallet_expires': wallet_expires,
            'wallet_warning': wallet_warning,
        })
    except Exception as e:
        return Response({'balance': 0.0, 'currency': 'FCFA', 'transactions': [], 'error': str(e)})
# force deploy Fri May 15 08:18:07 AM CEST 2026


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def api_chatbot(request):
    """POST /api/client/chatbot/ — proxy vers Claude API"""
    import requests as req

    messages = request.data.get('messages', [])
    if not messages:
        return Response({'error': 'Messages requis'}, status=400)

    ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
    if not ANTHROPIC_KEY:
        return Response({'error': 'Service non disponible'}, status=503)

    SYSTEM = """Tu es l'assistant virtuel de FAGNI, plateforme de blanchisserie à domicile à Abidjan.
Zone : Riviera 3, Cocody, Riviera. Délai : 48-72h.
Prix : 500 FCFA/article + 2 000 FCFA livraison AR. Le sac est une estimation visuelle — prix confirmé au comptage à la collecte.
Articles refusés : sous-vêtements, chaussettes.
Articles sur devis : costume, boubou, kaba, agbada, robe longue, manteau, veste, drap.
OPS WhatsApp : +225 01 42 29 99 49. Réponds en français, sois chaleureux et concis."""

    try:
        resp = req.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 500,
                'system': SYSTEM,
                'messages': messages
            },
            timeout=30
        )
        data = resp.json()
        text = data.get('content', [{}])[0].get('text', 'Désolé, contactez OPS.')
        return Response({'reply': text})
    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def api_rate_order(request, order_id):
    """POST /api/client/orders/<id>/rate/ — noter une commande"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from orders.models import Customer
        customer = Customer.objects.get(id=payload['cid'])
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from orders.models import Order, OrderRating
        order = Order.objects.get(id=order_id, customer=customer)

        if order.status != 'done':
            return Response({'error': 'Commande non livrée'}, status=400)

        score   = int(request.data.get('score', 0))
        comment = request.data.get('comment', '').strip()

        if not 1 <= score <= 5:
            return Response({'error': 'Score entre 1 et 5'}, status=400)

        rating, created = OrderRating.objects.update_or_create(
            order=order,
            defaults={'score': score, 'comment': comment}
        )
        return Response({
            'success': True,
            'score': score,
            'message': 'Merci pour votre avis !'
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def api_welcome_notification(request):
    """POST /api/client/welcome/ — message bienvenue WhatsApp"""
    import urllib.parse
    phone = request.data.get('phone', '')
    name  = request.data.get('name', 'Client')

    if not phone:
        return Response({'error': 'Phone requis'}, status=400)

    msg = f"""Bienvenue chez FAGNI {name} ! 🎉

Votre blanchisserie à domicile à Abidjan.

🧺 Comment commander :
→ fagni-client.vercel.app

💰 Tarif : 500 FCFA/article + 2 000 FCFA livraison AR
📌 Prix confirmé au comptage à la collecte
⏱️ Délai : 48h

Des questions ? Écrivez-nous ici !
⚡ FAGNI — Riviera 3, Abidjan"""

    from orders.config_models import GlobalPricingSettings as _GPS_OPS2
    _ops_raw2 = GlobalPricingSettings.get_solo().whatsapp_ops_number or "0142299949"
    _ops_num2 = "225" + _ops_raw2.lstrip("0") if not _ops_raw2.startswith("225") else _ops_raw2
    wa_link = f"https://wa.me/{_ops_num2}?text={urllib.parse.quote(msg)}"
    return Response({'success': True, 'wa_link': wa_link})


# ═══════════════════════════════════════════════════════════
# ABONNEMENT — estimation et reservation (24 juillet 2026)
# Construit directement en V1, jamais un pont vers V2.
# ═══════════════════════════════════════════════════════════

@api_view(['POST'])
@permission_classes([AllowAny])
def api_abonnement_estimer(request):
    """
    POST /api/abonnement/estimer/
    Payload : {"pack": "confort", "taille_sac": "M"}
    Anonyme par construction - avant creation de compte.
    """
    from orders.models import AbonnementPricingRule

    pack = request.data.get('pack', '').strip()
    taille_sac = request.data.get('taille_sac', '').strip()

    if pack not in ('essentiel', 'confort') or taille_sac not in ('S', 'M', 'XL'):
        return Response({'error': 'pack et taille_sac requis et valides'}, status=400)

    try:
        regle = AbonnementPricingRule.objects.get(pack=pack, taille_sac=taille_sac)
    except AbonnementPricingRule.DoesNotExist:
        return Response({'error': "Cette offre n'est pas encore configuree."}, status=503)

    if not regle.is_active:
        return Response({'error': "Cette offre n'est pas actuellement proposee."}, status=422)

    prix_hebdo = regle.prix_hebdomadaire
    prix_mensuel = regle.prix_mensuel
    return Response({
        'disponible': True,
        'pack': regle.get_pack_display(),
        'taille_sac': regle.get_taille_sac_display(),
        'prix': float(prix_hebdo),
        'prix_mensuel': float(prix_mensuel),
        'prix_quotidien_equivalent': round(float(prix_mensuel) / 30, 0),
        'offre_lancement': True,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def api_abonnement_reserver(request):
    """
    POST /api/abonnement/reserver/
    Payload : {"telephone": "...", "nom": "...", "pack": "...", "taille_sac": "...", "jour_collecte": 0, "jour_livraison": 3}
    Cree (ou reutilise) le Customer, cree l'Abonnement avec le prix
    verrouille au moment de la reservation (BOS 4.1).
    """
    from orders.models import Abonnement, AbonnementPricingRule, Order

    telephone = request.data.get('telephone', '').strip()
    nom = request.data.get('nom', '').strip()
    pack = request.data.get('pack', '').strip()
    taille_sac = request.data.get('taille_sac', '').strip()
    jour_collecte = request.data.get('jour_collecte')
    jour_livraison = request.data.get('jour_livraison')
    essai_origine_id = request.data.get('essai_origine')  # optionnel (Lot 5) - conversion post-essai

    if not all([telephone, nom, pack, taille_sac]) or jour_collecte is None or jour_livraison is None:
        return Response({'error': 'Tous les champs sont requis'}, status=400)

    essai_origine = None
    if essai_origine_id:
        try:
            essai_origine = Order.objects.get(id=essai_origine_id, order_origin='routine_trial')
        except Order.DoesNotExist:
            return Response({'error': "Essai d'origine introuvable."}, status=404)

        # Un essai ne peut produire qu'un seul abonnement actif (spec section 6)
        if Abonnement.objects.filter(essai_origine=essai_origine, statut='actif').exists():
            return Response({'error': 'Cet essai a deja ete converti en abonnement.'}, status=409)

    try:
        regle = AbonnementPricingRule.objects.get(pack=pack, taille_sac=taille_sac, is_active=True)
    except AbonnementPricingRule.DoesNotExist:
        return Response({'error': "Cette offre n'est pas disponible."}, status=422)

    customer, _ = Customer.objects.get_or_create(
        phone=telephone,
        defaults={'name': nom, 'address': ''},
    )

    abonnement, cree = Abonnement.objects.get_or_create(
        customer=customer, statut='actif', pack=pack, taille_sac=taille_sac,
        defaults={
            'jour_collecte': jour_collecte,
            'jour_livraison': jour_livraison,
            'prix_verrouille': regle.prix_mensuel,
            'essai_origine': essai_origine,
        },
    )

    if cree:
        try:
            from fagni.notifications import notif_ops_retrait
            notif_ops_retrait(
                f"Nouvel abonnement {nom} ({telephone})",
                float(regle.prix_mensuel),
                f"abonnement:{abonnement.id}",
            )
        except Exception:
            import logging
            logging.getLogger("fagni.orders.client_api").exception(
                "Echec silencieux notif nouvel abonnement | abonnement_id=%s", abonnement.id
            )

        if essai_origine:
            from orders.models import EvenementRoutine
            EvenementRoutine.objects.create(
                customer=customer, type_evenement='abonnement_active',
                donnees={'abonnement_id': abonnement.id, 'essai_origine_id': essai_origine.id},
            )

    return Response({
        'already_reserved': not cree,
        'abonnement_id': abonnement.id,
        'statut': abonnement.get_statut_display(),
    }, status=200)


@api_view(['GET'])
@permission_classes([AllowAny])
def api_mon_abonnement_v1(request):
    """GET /api/abonnement/mon-abonnement/?telephone=..."""
    from orders.models import Abonnement

    telephone = request.GET.get('telephone', '').strip()
    if not telephone:
        return Response({'error': 'telephone requis'}, status=400)

    try:
        customer = Customer.objects.get(phone=telephone)
    except Customer.DoesNotExist:
        return Response({'abonnement': None})

    abonnement = Abonnement.objects.filter(customer=customer).order_by('-created_at').first()
    if not abonnement:
        return Response({'abonnement': None})

    return Response({
        'abonnement': {
            'pack': abonnement.get_pack_display(),
            'taille_sac': abonnement.get_taille_sac_display(),
            'statut': abonnement.get_statut_display(),
            'prix': float(abonnement.prix_verrouille),
            'cree_le': abonnement.created_at.strftime('%d/%m/%Y'),
            'jour_collecte': abonnement.get_jour_collecte_display(),
            'jour_livraison': abonnement.get_jour_livraison_display(),
        }
    })


# ═══════════════════════════════════════════════════════════
# ROUTINE — essai ponctuel (Lot 3, 27 juillet 2026)
# N'utilise JAMAIS le moteur "Pricing v3.0" par article
# (incompatible - voir spec section 0). Prix pose directement
# depuis AbonnementPricingRule, comme pour un vrai abonnement.
# ═══════════════════════════════════════════════════════════

@api_view(['POST'])
@permission_classes([AllowAny])
def api_routine_essai(request):
    """
    POST /api/routine/essai/
    Payload : {telephone, nom, routine, pack, taille_sac, adresse, jour_collecte_souhaite}
    Anonyme par construction. Cree une vraie Order (order_origin='routine_trial'),
    jamais un abonnement, jamais via api_create_order.
    """
    from datetime import date, timedelta
    from orders.models import AbonnementPricingRule

    telephone = request.data.get('telephone', '').strip()
    nom = request.data.get('nom', '').strip()
    routine = request.data.get('routine', '').strip()
    pack = request.data.get('pack', '').strip()
    taille_sac = request.data.get('taille_sac', '').strip()
    adresse = request.data.get('adresse', '').strip()
    jour_collecte_souhaite = request.data.get('jour_collecte_souhaite')

    if not all([telephone, nom, routine, pack, taille_sac]):
        return Response({'error': 'Champs requis manquants'}, status=400)

    if routine not in dict(Order.ROUTINE_CHOICES):
        return Response({'error': 'Routine invalide'}, status=400)

    try:
        regle = AbonnementPricingRule.objects.get(pack=pack, taille_sac=taille_sac, is_active=True)
    except AbonnementPricingRule.DoesNotExist:
        return Response({'error': "Cette offre n'est pas disponible."}, status=422)

    customer, _ = Customer.objects.get_or_create(
        phone=telephone,
        defaults={'name': nom, 'address': adresse},
    )

    # Idempotence : un essai routine_trial deja en cours (non termine) bloque un nouveau
    essai_existant = Order.objects.filter(
        customer=customer, order_origin='routine_trial',
    ).exclude(status__in=['delivered', 'cancelled']).first()

    if essai_existant:
        return Response({
            'already_exists': True,
            'order_id': essai_existant.id,
            'status': essai_existant.status,
        }, status=200)

    today = date.today()
    pickup_date = today + timedelta(days=1)
    delivery_date = pickup_date + timedelta(days=2)

    order = Order.objects.create(
        customer=customer,
        pricing_mode='bag',  # jamais 'routine_essai' - audit confirme, evite de casser is_bag/is_item
        bag_size=taille_sac,
        pickup_address=adresse or getattr(customer, 'address', '') or '',
        delivery_address=adresse or getattr(customer, 'address', '') or '',
        pickup_scheduled_date=pickup_date,
        delivery_scheduled_date=delivery_date,
        status='pending',
        order_origin='routine_trial',
        routine_proposee=routine,
        routine_choisie=routine,
        total_client_ttc=regle.prix_hebdomadaire,
        notes=f"Essai Routine - {dict(Order.ROUTINE_CHOICES).get(routine)}",
    )

    return Response({
        'already_exists': False,
        'order_id': order.id,
        'routine': routine,
        'prix': float(regle.prix_hebdomadaire),
        'status': order.status,
    }, status=201)


# ═══════════════════════════════════════════════════════════
# ROUTINE — essai ponctuel (Lot 3, 27 juillet 2026)
# N'utilise JAMAIS le moteur "Pricing v3.0" par article
# (incompatible - voir spec section 0). Prix pose directement
# depuis AbonnementPricingRule, comme pour un vrai abonnement.
# ═══════════════════════════════════════════════════════════

@api_view(['POST'])
@permission_classes([AllowAny])
def api_routine_essai(request):
    """
    POST /api/routine/essai/
    Payload : {telephone, nom, routine, pack, taille_sac, adresse, jour_collecte_souhaite}
    Anonyme par construction. Cree une vraie Order (order_origin='routine_trial'),
    jamais un abonnement, jamais via api_create_order.
    """
    from datetime import date, timedelta
    from orders.models import AbonnementPricingRule

    telephone = request.data.get('telephone', '').strip()
    nom = request.data.get('nom', '').strip()
    routine = request.data.get('routine', '').strip()
    pack = request.data.get('pack', '').strip()
    taille_sac = request.data.get('taille_sac', '').strip()
    adresse = request.data.get('adresse', '').strip()
    jour_collecte_souhaite = request.data.get('jour_collecte_souhaite')

    if not all([telephone, nom, routine, pack, taille_sac]):
        return Response({'error': 'Champs requis manquants'}, status=400)

    if routine not in dict(Order.ROUTINE_CHOICES):
        return Response({'error': 'Routine invalide'}, status=400)

    try:
        regle = AbonnementPricingRule.objects.get(pack=pack, taille_sac=taille_sac, is_active=True)
    except AbonnementPricingRule.DoesNotExist:
        return Response({'error': "Cette offre n'est pas disponible."}, status=422)

    customer, _ = Customer.objects.get_or_create(
        phone=telephone,
        defaults={'name': nom, 'address': adresse},
    )

    # Idempotence : un essai routine_trial deja en cours (non termine) bloque un nouveau
    essai_existant = Order.objects.filter(
        customer=customer, order_origin='routine_trial',
    ).exclude(status__in=['delivered', 'cancelled']).first()

    if essai_existant:
        return Response({
            'already_exists': True,
            'order_id': essai_existant.id,
            'status': essai_existant.status,
        }, status=200)

    today = date.today()
    pickup_date = today + timedelta(days=1)
    delivery_date = pickup_date + timedelta(days=2)

    order = Order.objects.create(
        customer=customer,
        pricing_mode='bag',  # jamais 'routine_essai' - audit confirme, evite de casser is_bag/is_item
        bag_size=taille_sac,
        pickup_address=adresse or getattr(customer, 'address', '') or '',
        delivery_address=adresse or getattr(customer, 'address', '') or '',
        pickup_scheduled_date=pickup_date,
        delivery_scheduled_date=delivery_date,
        status='pending',
        order_origin='routine_trial',
        routine_proposee=routine,
        routine_choisie=routine,
        total_client_ttc=regle.prix_hebdomadaire,
        notes=f"Essai Routine - {dict(Order.ROUTINE_CHOICES).get(routine)}",
    )

    return Response({
        'already_exists': False,
        'order_id': order.id,
        'routine': routine,
        'prix': float(regle.prix_hebdomadaire),
        'status': order.status,
    }, status=201)


@api_view(['GET'])
@permission_classes([AllowAny])
def api_routine_essai_detail(request, order_id):
    """
    GET /api/routine/essai/<id>/
    Lecture publique des infos d'un essai, pour prerempllir l'ecran de
    conversion post-essai (Lot 5) - jamais de donnee sensible au-dela
    de ce que le client connait deja.
    """
    from orders.models import Order, AbonnementPricingRule

    try:
        order = Order.objects.get(id=order_id, order_origin='routine_trial')
    except Order.DoesNotExist:
        return Response({'error': 'Essai introuvable'}, status=404)

    if order.satisfaction_reponse not in ('positive', 'resolved'):
        return Response({'error': "Cette proposition n'est pas encore disponible."}, status=422)

    routine = order.routine_choisie or order.routine_proposee
    pack = 'confort'  # seul pack commercialise au lancement
    taille_sac = order.bag_size

    try:
        regle = AbonnementPricingRule.objects.get(pack=pack, taille_sac=taille_sac, is_active=True)
    except AbonnementPricingRule.DoesNotExist:
        return Response({'error': "Cette offre n'est plus disponible."}, status=422)

    return Response({
        'order_id': order.id,
        'routine': routine,
        'pack': pack,
        'taille_sac': taille_sac,
        'prix': float(regle.prix_mensuel),
        'telephone': order.customer.phone if order.customer else '',
        'nom': order.customer.name if order.customer else '',
    })


# ═══════════════════════════════════════════════════════════
# ACCUEIL — endpoint reel (28 juillet 2026)
# Premier ecran Accueil jamais construit en production - point
# d'entree une fois le client authentifie (JWT 'cid').
# ═══════════════════════════════════════════════════════════

@api_view(['GET'])
@permission_classes([AllowAny])
def api_client_accueil(request):
    """
    GET /api/client/accueil/
    Auth requise (Bearer JWT client). Retourne le ViewModel exact
    attendu par l'ecran Accueil - aucune donnee inventee cote
    frontend, tout vient d'ici.
    """
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        customer = Customer.objects.get(id=payload['cid'])
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    # Commande active = statut reellement non-terminal (STATUS_CHOICES
    # reel de Order : pending, in_progress, done, canceled)
    commande = Order.objects.filter(
        customer=customer, status__in=['pending', 'in_progress'],
    ).order_by('-created_at').first()

    commande_active = None
    if commande:
        libelles = {'pending': 'En attente de prise en charge', 'in_progress': 'En cours de traitement'}
        etapes = {'pending': 1, 'in_progress': 2}
        routine_label = None
        if commande.routine_choisie:
            routine_label = commande.get_routine_choisie_display()
        elif commande.routine_proposee:
            routine_label = commande.get_routine_proposee_display()

        commande_active = {
            'id': commande.id,
            'service': routine_label or 'Pressing',
            'libelleStatut': libelles.get(commande.status, commande.status),
            'etape': etapes.get(commande.status, 1),
            'etapesTotal': 3,
        }

    # Un seul service reel aujourd'hui - pas d'invention d'autres
    # services, juste la structure prete a en recevoir demain.
    services = [
        {'id': 'pressing', 'nom': 'Pressing', 'disponible': True},
    ]

    return Response({
        'status': 'ready',
        'utilisateur': {'prenom': (customer.name or '').split(' ')[0] or 'Client'},
        'commandeActive': commande_active,
        'services': services,
        'fidelite': None,  # pas encore de systeme de points reel - jamais invente
    })
