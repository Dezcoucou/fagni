# fagni/middleware.py
from django.utils import translation

ADMIN_PREFIX = "/admin/"

class ForceAdminLanguageMiddleware:
    """Force la langue FR pour toutes les URLs d'admin, quelle que soit la session/cookie."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        deactivate_after = False
        if request.path.startswith(ADMIN_PREFIX):
            translation.activate("fr")
            request.LANGUAGE_CODE = "fr"
            deactivate_after = True

        response = self.get_response(request)

        if deactivate_after:
            translation.deactivate()

        return response