import jwt
from decimal import Decimal
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Sum, F, Value, DecimalField, Q
from django.db.models.functions import Coalesce, Cast

from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Order, Customer


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
    total = float(o.total_client_ttc or getattr(o, 'total', 0) or getattr(o, 'items_total', 0) or 0)
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
    customer = Customer.objects.filter(phone=phone).first()
    if not customer:
        return Response({'error': 'Numéro non reconnu'}, status=404)
    return Response({
        'access': _make_token(customer),
        'customer': {'name': customer.name, 'phone': customer.phone},
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

    # Vérifier si client existe déjà
    existing = Customer.objects.filter(phone=phone).first()
    if existing:
        return Response({
            'access': _make_token(existing),
            'customer': {'name': existing.name, 'phone': existing.phone},
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
    return Response({
        'customer': {
            'name':    customer.name,
            'phone':   customer.phone,
            'initial': (customer.name or 'F')[0].upper(),
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

    total = float(order.total_client_ttc or getattr(order,'total',0) or order.items_total or 0)
    amount_paid = float(getattr(order, 'amount_paid', 0) or 0)

    items = []
    for item in order.items.all():
        items.append({
            'id':         item.id,
            'name':       getattr(item, 'name', None) or getattr(item, 'description', None) or 'Article',
            'quantity':   float(getattr(item, 'quantity', 1) or 1),
            'unit_price': float(getattr(item, 'unit_price', 0) or 0),
        })

    return Response({
        'id':             order.id,
        'code':           order.code or str(order.id),
        'status':         order.status,
        'payment_status': getattr(order, 'payment_status', 'unpaid'),
        'service_type':   getattr(order, 'service_type', None) or 'FAGNI',
        'total':          total,
        'amount_paid':    amount_paid,
        'remaining':      max(0, total - amount_paid),
        'created_at':     order.created_at.isoformat() if order.created_at else None,
        'items':          items,
    })





@api_view(['POST'])
@authentication_classes([ClientAuth])
@permission_classes([])
def api_create_order(request):
    """POST /api/client/orders/create/"""
    from orders.utils.pricing import BAG_PRICING
    from datetime import date, time as dtime, timedelta

    bag_size      = (request.data.get('bag_size') or '').strip()
    pickup_addr   = (request.data.get('pickup_address') or '').strip()
    pickup_lat    = request.data.get('pickup_lat')
    pickup_lng    = request.data.get('pickup_lng')
    pickup_slot   = (request.data.get('pickup_slot') or '').strip()
    articles      = request.data.get('articles', [])

    if bag_size not in BAG_PRICING:
        return Response({'error': 'Taille de sac invalide'}, status=400)

    customer = request.user
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

    # Frais FAGNI : 10% avec minimum 500 FCFA
    bag_price   = int(BAG_PRICING[bag_size]['price'])
    raw_fee     = int(bag_price * 0.10)
    service_fee = max(500, raw_fee)
    total       = bag_price + service_fee

    notes_parts = []
    if articles:
        notes_parts.append(f"Articles: {articles}")
    if pickup_slot:
        notes_parts.append(f"Creneau: {pickup_slot}")

    try:
        # Calcul financier via pricing engine
        from orders.pricing_engine import calculate_order
        pricing = calculate_order(total, 2000)

        order = Order.objects.create(
            customer=customer,
            bag_size=bag_size,
            pickup_address=pickup_addr or getattr(customer, 'address', '') or '',
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            pickup_scheduled_date=pickup_date,
            pickup_scheduled_time=pickup_time,
            delivery_scheduled_date=delivery_date,
            delivery_scheduled_time=dtime(12, 0),
            service_fee=service_fee,
            notes=' | '.join(notes_parts),
            status='pending',
            delivery_fee=pricing['delivery_fee'],
            commission_delivery_ht=pricing['marge_livraison'],
            commission_laundry_ht=pricing['commission_pressing'],
            amount_driver_partner=pricing['part_livreur'],
            amount_laundry_partner=pricing['part_pressing'],
            fagni_revenue_ht=pricing['total_fagni'],
        )
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
def api_rate_order(request, order_id):
    """POST /api/client/orders/<id>/rate/ — {score, comment}"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    try:
        import jwt
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        from orders.models import Customer
        customer = Customer.objects.get(id=payload['cid'])
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from orders.models import Order, OrderRating
        order = Order.objects.get(id=order_id, customer=customer)
        score = int(request.data.get('score', 0))
        comment = request.data.get('comment', '').strip()

        if not 1 <= score <= 5:
            return Response({'error': 'Score entre 1 et 5'}, status=400)

        rating, created = OrderRating.objects.update_or_create(
            order=order,
            defaults={'score': score, 'comment': comment}
        )
        return Response({'success': True, 'score': score, 'created': created})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


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
        })

    # Bags depuis PricingConfig
    pricing_configs = PricingConfig.objects.filter(is_active=True).order_by('pressing_amount')
    bags = {}
    for config in pricing_configs:
        pricing = calculate_order(config.pressing_amount, config.delivery_fee)
        bags[config.bag_size] = {
            'label':       config.label,
            'price':       pricing['total_client'],
            'pressing':    config.pressing_amount,
            'delivery':    config.delivery_fee,
            'service_fee': pricing['service_fee'],
            'max_items':   {'small':15,'medium':25,'large':40}.get(config.bag_size, 15),
            'max_weight_kg': {'small':5,'medium':10,'large':15}.get(config.bag_size, 5),
        }

    return Response({
        'bags':    bags,
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
                'done':    status in ['in_progress', 'done'],
                'active':  status == 'pending' and order.pickup_driver_id is not None,
            },
            {
                'id':      'pressing',
                'label':   'Au pressing',
                'desc':    'Vos vêtements sont en cours de traitement',
                'emoji':   '🧺',
                'done':    status in ['in_progress', 'done'],
                'active':  status == 'in_progress',
            },
            {
                'id':      'ready',
                'label':   'Prêt pour livraison',
                'desc':    'Vos vêtements sont propres et prêts',
                'emoji':   '✅',
                'done':    status == 'done',
                'active':  False,
            },
            {
                'id':      'delivery',
                'label':   'Livraison en cours',
                'desc':    'Le livreur arrive chez vous',
                'emoji':   '🚗',
                'done':    status == 'done' and 'LIVRAISON:Confirmée' in notes,
                'active':  status == 'done' and order.delivery_partner_id is not None,
            },
            {
                'id':      'delivered',
                'label':   'Livré',
                'desc':    'Vos vêtements ont été livrés',
                'emoji':   '🎉',
                'done':    status == 'done' and 'LIVRAISON:Confirmée' in notes,
                'active':  False,
            },
        ]

        # Infos livreur (anonymisé)
        pickup_driver = None
        if order.pickup_driver:
            pickup_driver = {
                'vehicle': order.pickup_driver.vehicle_type or 'moto',
                'initials': order.pickup_driver.name[0].upper() if order.pickup_driver.name else 'L',
            }

        delivery_driver = None
        if order.delivery_partner:
            delivery_driver = {
                'vehicle': order.delivery_partner.vehicle_type or 'moto',
                'initials': order.delivery_partner.name[0].upper() if order.delivery_partner.name else 'L',
            }

        # Articles collectés
        articles_count = 0
        for line in notes.split('\n'):
'):
            if line.startswith('COLLECTE:'):
                try:
                    articles_count = int(line.split(':')[1].split(' ')[0])
                except: pass

        return Response({
            'order_id':       order.id,
            'code':           order.code or str(order.id),
            'status':         status,
            'payment_status': payment,
            'bag_size':       order.bag_size or '',
            'total':          float(order.total or 0),
            'steps':          steps,
            'pickup_driver':  pickup_driver,
            'delivery_driver': delivery_driver,
            'articles_count': articles_count,
            'has_partner':    order.laundry_partner_id is not None,
            'created_at':     order.created_at.isoformat() if order.created_at else None,
        })
    except Exception as e:
        return Response({'error': str(e)}, status=404)
