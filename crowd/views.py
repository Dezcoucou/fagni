"""
Vues frontend pour l'interface cotransporteur Crowd.

Pages :
- /crowd/login/ : page de connexion
- /crowd/dashboard/ : dashboard des offres disponibles
- /crowd/missions/ : liste des missions acceptées
"""
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.conf import settings

import jwt


def crowd_login(request):
    """
    GET /crowd/login/ : page de connexion cotransporteur
    POST /crowd/login/ : authentification (retourne token JWT)
    """
    if request.method == 'GET':
        return render(request, 'crowd/login.html')
    
    # POST : authentification
    # Pour l'instant, on simule une auth simple (à remplacer par OAuth/phone+code)
    cotransporter_id = request.POST.get('cotransporter_id')
    
    if not cotransporter_id:
        return render(request, 'crowd/login.html', {'error': 'ID cotransporteur requis'})
    
    # Vérifier que le cotransporter existe
    from crowd.models import Cotransporter
    try:
        cotransporter = Cotransporter.objects.get(id=cotransporter_id, is_active=True)
    except Cotransporter.DoesNotExist:
        return render(request, 'crowd/login.html', {'error': 'Cotransporteur non trouvé'})
    
    # Générer token JWT
    payload = {
        'cotransporter_id': cotransporter.id,
        'name': cotransporter.user.get_full_name() or cotransporter.user.username,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')
    
    # Rediriger vers le dashboard avec le token
    response = redirect('/crowd/dashboard/')
    response.set_cookie('crowd_token', token, httponly=True, max_age=86400)  # 24h
    return response


def crowd_dashboard(request):
    """
    GET /crowd/dashboard/ : dashboard des offres disponibles
    """
    # Vérifier le token
    token = request.COOKIES.get('crowd_token')
    if not token:
        return redirect('/crowd/login/')
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        cotransporter_id = payload.get('cotransporter_id')
        cotransporter_name = payload.get('name', 'Cotransporteur')
    except jwt.InvalidTokenError:
        return redirect('/crowd/login/')
    
    return render(request, 'crowd/dashboard.html', {
        'cotransporter_id': cotransporter_id,
        'cotransporter_name': cotransporter_name,
    })


def crowd_missions(request):
    """
    GET /crowd/missions/ : liste des missions acceptées
    """
    # Vérifier le token
    token = request.COOKIES.get('crowd_token')
    if not token:
        return redirect('/crowd/login/')
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        cotransporter_id = payload.get('cotransporter_id')
        cotransporter_name = payload.get('name', 'Cotransporteur')
    except jwt.InvalidTokenError:
        return redirect('/crowd/login/')
    
    return render(request, 'crowd/missions.html', {
        'cotransporter_id': cotransporter_id,
        'cotransporter_name': cotransporter_name,
    })


@require_http_methods(["POST"])
def crowd_logout(request):
    """
    POST /crowd/logout/ : déconnexion
    """
    response = redirect('/crowd/login/')
    response.delete_cookie('crowd_token')
    return response
