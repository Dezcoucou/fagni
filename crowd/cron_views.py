import logging
import os
import secrets

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from crowd.expiration import expire_stale_offers

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def expire_crowd_offers(request):
    expected_secret = os.environ.get("CROWD_CRON_SECRET", "")

    if not expected_secret:
        return JsonResponse(
            {"ok": False, "error": "Cron non configuré"},
            status=503,
        )

    authorization = request.headers.get("Authorization", "")
    expected_authorization = f"Bearer {expected_secret}"

    if not secrets.compare_digest(authorization, expected_authorization):
        return JsonResponse(
            {"ok": False, "error": "Accès refusé"},
            status=403,
        )

    try:
        result = expire_stale_offers()
        return JsonResponse({"ok": True, **result})
    except Exception:
        logger.exception("Échec du traitement d'expiration Crowd")
        return JsonResponse(
            {"ok": False, "error": "Erreur interne"},
            status=500,
        )
