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

    # Retraits en attente
    from orders.models import Paiement
    retraits = list(Paiement.objects.filter(
        note__contains='DEMANDE_RETRAIT',
        cash_paye=False
    ).values('id','partenaire_nom','montant','wave_number','created_at'))

    return Response({
        'pressings': pressings_data,
        'livreurs': livreurs_data,
        'total_a_payer': total_a_payer,
        'nb_commandes_done': nb_done,
        'retraits': retraits,
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


@api_view(['GET'])
@permission_classes([AllowAny])
def api_score_pressing(request, partner_id):
    """GET /api/ops/score/pressing/<id>/ — score pressing"""
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
    """GET /api/ops/score/livreur/<id>/ — score livreur"""
    from partners.models import DeliveryPartner
    from orders.models import Order
    from django.db.models import Sum

    try:
        driver = DeliveryPartner.objects.get(id=driver_id)
    except:
        return Response({'error': 'Livreur non trouvé'}, status=404)

    collectes = Order.objects.filter(pickup_driver=driver, status='done').count()
    livraisons = Order.objects.filter(delivery_partner=driver, status='done').count()
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
    remun_c = getattr(driver, 'remuneration_collecte', 1000) or 1000
    remun_l = getattr(driver, 'remuneration_livraison', 1000) or 1000
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


@api_view(['GET'])
@permission_classes([AllowAny])
def api_score_pressing(request, partner_id):
    """GET /api/ops/score/pressing/<id>/ — score pressing"""
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
    """GET /api/ops/score/livreur/<id>/ — score livreur"""
    from partners.models import DeliveryPartner
    from orders.models import Order
    from django.db.models import Sum

    try:
        driver = DeliveryPartner.objects.get(id=driver_id)
    except:
        return Response({'error': 'Livreur non trouvé'}, status=404)

    collectes = Order.objects.filter(pickup_driver=driver, status='done').count()
    livraisons = Order.objects.filter(delivery_partner=driver, status='done').count()
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
    remun_c = getattr(driver, 'remuneration_collecte', 1000) or 1000
    remun_l = getattr(driver, 'remuneration_livraison', 1000) or 1000
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


# ============================================================
# PARRAINAGE FAGNI
# ============================================================

import random
import string

def generer_code():
    """Générer un code parrainage unique."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


@api_view(['POST'])
@permission_classes([AllowAny])
def api_creer_parrainage(request):
    """POST /api/parrainage/creer/ — créer un lien de parrainage"""
    from orders.models import Parrainage

    parrain_type = request.data.get('parrain_type')
    parrain_id   = request.data.get('parrain_id')
    parrain_nom  = request.data.get('parrain_nom')

    if not all([parrain_type, parrain_id, parrain_nom]):
        return Response({'error': 'Données manquantes'}, status=400)

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

def dispatch_wallet_after_order(order):
    """
    Crédite automatiquement les wallets pressing et livreur
    après qu'une commande est marquée comme livrée.
    """
    from wallets.models import Wallet, WalletTransaction
    from orders.pricing_engine import calculate_order

    try:
        # Calculer les montants
        nb_articles = order.nb_articles or 15
        pricing = calculate_order(nb_articles, order.bag_size or 'small')

        # 1. Créditer le wallet du pressing
        if order.laundry_partner:
            try:
                wallet_pressing, _ = Wallet.objects.get_or_create(
                    laundry_partner=order.laundry_partner,
                    defaults={'currency': 'XOF', 'balance': 0}
                )
                montant_pressing = pricing['part_pressing']
                wallet_pressing.balance += montant_pressing
                wallet_pressing.save(update_fields=['balance', 'updated_at'])
                WalletTransaction.objects.create(
                    wallet=wallet_pressing,
                    order=order,
                    type='payout',
                    direction='credit',
                    amount=montant_pressing,
                    description=f"Commande {order.code} — {nb_articles} articles × 200 FCFA",
                )
            except Exception as e:
                print(f"Erreur wallet pressing: {e}")

        # 2. Créditer le wallet du livreur
        if order.pickup_driver or order.delivery_partner:
            driver = order.pickup_driver or order.delivery_partner
            try:
                wallet_livreur, _ = Wallet.objects.get_or_create(
                    delivery_partner=driver,
                    defaults={'currency': 'XOF', 'balance': 0}
                )
                remun_c = getattr(driver, 'remuneration_collecte', 1000) or 1000
                remun_l = getattr(driver, 'remuneration_livraison', 1000) or 1000
                montant_livreur = remun_c + remun_l
                wallet_livreur.balance += montant_livreur
                wallet_livreur.save(update_fields=['balance', 'updated_at'])
                WalletTransaction.objects.create(
                    wallet=wallet_livreur,
                    order=order,
                    type='payout',
                    direction='credit',
                    amount=montant_livreur,
                    description=f"Mission {order.code} — collecte + livraison",
                )
            except Exception as e:
                print(f"Erreur wallet livreur: {e}")

        return True
    except Exception as e:
        print(f"Erreur dispatch wallet: {e}")
        return False


@api_view(['POST'])
@permission_classes([AllowAny])
def api_wallet_solde(request):
    """POST /api/wallet/solde/ — solde wallet partenaire ou livreur"""
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

        return Response({
            'solde': float(wallet.balance),
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
    """POST /api/wallet/retrait/ — demande de retrait partenaire"""
    from wallets.models import Wallet
    from orders.models import Paiement

    partner_type = request.data.get('partner_type')
    partner_id   = request.data.get('partner_id')
    partner_nom  = request.data.get('partner_nom', '')
    montant      = int(request.data.get('montant', 0))
    wave_number  = request.data.get('wave_number', '')

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

        # Créer demande de retrait dans Paiement (en attente OPS)
        paiement = Paiement.objects.create(
            partenaire_type=partner_type,
            partenaire_id=partner_id,
            partenaire_nom=partner_nom,
            montant=montant,
            wave_number=wave_number or '',
            note='DEMANDE_RETRAIT — En attente validation OPS',
            cash_paye=False,
        )

        # Lien WhatsApp OPS pour notifier
        import urllib.parse
        msg = f"Demande retrait FAGNI\n{partner_nom}\nMontant : {montant:,} FCFA\nWave : {wave_number}\nA valider sur fagni-ops.vercel.app"
        wa_link = f"https://wa.me/2250142299949?text={urllib.parse.quote(msg)}"

        return Response({
            'success': True,
            'paiement_id': paiement.id,
            'montant': montant,
            'solde_actuel': float(wallet.balance),
            'wa_link': wa_link,
            'message': f'Demande de {montant:,} FCFA enregistree. OPS vous paiera sous 24-48h.'
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)
