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
            'total':          float(o.total_client_ttc or o.total or 0),
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
    partners = list(LaundryPartner.objects.filter(is_active=True).values('id','name','phone','wave_number'))

    # Livreurs
    from partners.models import DeliveryPartner
    drivers = list(DeliveryPartner.objects.filter(is_active=True).values('id','name','phone','wave_number'))

    return Response({'orders': orders, 'stats': stats, 'partners': partners, 'drivers': drivers})


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
        driver_type = request.data.get('driver_type', 'delivery')
        if driver_type == 'pickup':
            order.pickup_driver = driver
            order.save(update_fields=['pickup_driver', 'updated_at'])
        else:
            order.delivery_partner = driver
            order.save(update_fields=['delivery_partner', 'updated_at'])
        return Response({'success': True, 'driver': driver.name if driver else None, 'type': driver_type})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def ops_mark_paid(request, order_id):
    """POST /api/ops/orders/<id>/mark-paid/ — marquer commande comme payée"""
    try:
        _check_ops(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order
    try:
        order = Order.objects.get(id=order_id)
        channel = request.data.get('channel', 'wave')
        reference = request.data.get('reference', '').strip()

        order.payment_status = 'paid'
        order.amount_paid = order.total
        order.payment_declared_channel = channel
        order.payment_declared_reference = reference
        order.save(update_fields=[
            'payment_status', 'amount_paid',
            'payment_declared_channel', 'payment_declared_reference',
            'updated_at'
        ])
        return Response({
            'success': True,
            'payment_status': 'paid',
            'amount': float(order.total or 0),
            'channel': channel
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
        partner = LaundryPartner.objects.create(
            name=request.data.get('name','').strip(),
            phone=request.data.get('phone','').strip(),
            wave_number=request.data.get('wave_number','').strip(),
            city=request.data.get('city','Abidjan').strip(),
            address=request.data.get('address','').strip(),
            lat=request.data.get('lat') or None,
            lng=request.data.get('lng') or None,
            is_active=True
        )
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
        driver = DeliveryPartner.objects.create(
            name=request.data.get('name','').strip(),
            phone=request.data.get('phone','').strip(),
            wave_number=request.data.get('wave_number','').strip(),
            vehicle_type=request.data.get('vehicle_type','moto').strip(),
            city=request.data.get('city','Abidjan').strip(),
            is_active=True,
            remuneration_collecte=int(request.data.get('remuneration_collecte', 1000) or 1000),
            remuneration_livraison=int(request.data.get('remuneration_livraison', 1000) or 1000),
        )
        return Response({'success': True, 'id': driver.id, 'name': driver.name})
    except Exception as e:
        return Response({'error': str(e)}, status=400)


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
        'id','name','phone','city','address'
    ))
    drivers = list(DeliveryPartner.objects.filter(is_active=True).values(
        'id','name','phone','city','vehicle_type'
    ))
    return Response({'partners': partners, 'drivers': drivers})


@api_view(['GET'])
@permission_classes([AllowAny])
def api_ops_paiements(request):
    """GET /api/ops/paiements/ — cumul gains pressings et livreurs"""
    from partners.models import LaundryPartner, DeliveryPartner
    from orders.models import Order
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
            'deja_paye': 0,
        })

    # Livreurs
    livreurs_data = []
    for d in DeliveryPartner.objects.filter(is_active=True):
        missions = Order.objects.filter(
            pickup_driver=d,
            status='done'
        )
        missions_collecte  = Order.objects.filter(pickup_driver=d, status='done').count()
        missions_livraison = Order.objects.filter(delivery_partner=d, status='done').count()
        nb = missions_collecte + missions_livraison
        remun_collecte  = getattr(d, 'remuneration_collecte', 1000) or 1000
        remun_livraison = getattr(d, 'remuneration_livraison', 1000) or 1000
        a_payer = (missions_collecte * remun_collecte) + (missions_livraison * remun_livraison)
        livreurs_data.append({
            'id': d.id,
            'name': d.name,
            'phone': d.phone or '',
            'wave_number': getattr(d, 'wave_number', '') or '',
            'nb_missions': nb,
            'a_payer': int(a_payer),
            'deja_paye': 0,
        })

    total_a_payer = sum(p['a_payer'] for p in pressings_data) + sum(l['a_payer'] for l in livreurs_data)
    nb_done = Order.objects.filter(status='done').count()

    return Response({
        'pressings': pressings_data,
        'livreurs': livreurs_data,
        'total_a_payer': total_a_payer,
        'nb_commandes_done': nb_done,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def api_ops_enregistrer_paiement(request):
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
    """GET /api/ops/revenus/ — revenus FAGNI"""
    from orders.models import Order
    from django.db.models import Sum
    from datetime import datetime, timedelta

    today = datetime.now().date()
    debut_semaine = today - timedelta(days=today.weekday())
    debut_mois = today.replace(day=1)

    def stats(qs):
        return {
            'nb': qs.count(),
            'total_client': int(qs.aggregate(s=Sum('total_client_ttc'))['s'] or 0),
            'revenus_fagni': int(qs.aggregate(s=Sum('fagni_revenue_ht'))['s'] or 0),
            'paye_pressing': int(qs.aggregate(s=Sum('amount_laundry_partner'))['s'] or 0),
        }

    done = Order.objects.filter(status='done')

    return Response({
        'total':        stats(done),
        'semaine':      stats(done.filter(created_at__date__gte=debut_semaine)),
        'mois':         stats(done.filter(created_at__date__gte=debut_mois)),
        'en_attente':   Order.objects.filter(status='pending').count(),
        'en_cours':     Order.objects.filter(status='in_progress').count(),
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def api_ops_rapport_hebdo(request):
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
    revenu_fagni = int(livrees_semaine.aggregate(
        s=Sum('fagni_revenue_ht'))['s'] or 0)
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
