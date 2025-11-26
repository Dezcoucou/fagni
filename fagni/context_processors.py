from django.conf import settings

def google_keys(request):
    return {
        "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
        "GOOGLE_DISTANCE_MATRIX_API_KEY": getattr(settings, "GOOGLE_DISTANCE_MATRIX_API_KEY", ""),
    }
