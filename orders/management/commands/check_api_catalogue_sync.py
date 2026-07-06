"""
Commande Django : python3 manage.py check_api_catalogue_sync

Compare les routes /api/ reellement enregistrees dans les URLconf Django
avec ce que documente le FOS-202 (API Catalogue). Signale :
  - les routes du code absentes du catalogue (dette documentaire)
  - les routes documentees qui n'existent plus dans le code (documentation perimee)
"""
from django.core.management.base import BaseCommand
from django.urls import get_resolver


# Routes documentees dans le FOS-202, version 1.0 du 5 juillet 2026.
# Format : chemin normalise (parametres remplaces par {id} generique).
DOCUMENTED_ROUTES = {
    "api/client/login/", "api/client/home/", "api/client/pricing/bags/",
    "api/client/pricing/detail/", "api/client/create-order/", "api/client/orders/",
    "api/client/orders/{id}/", "api/client/orders/{id}/cancel/",
    "api/client/orders/{id}/rate/", "api/client/orders/{id}/report-litige/",
    "api/client/wallet/", "api/client/parrainage/", "api/client/articles/",
    "api/client/register/", "api/client/order-tracking/", "api/client/chatbot/",

    "api/driver/login/", "api/driver/missions/", "api/driver/pending-mission/",
    "api/driver/confirm-pickup/", "api/driver/confirm-delivery/",
    "api/driver/orders/{id}/photo/", "api/driver/dropoff/",
    "api/driver/delivery-proof/{id}/", "api/driver/wallet/",
    "api/driver/toggle-status/", "api/driver/update-location/",
    "api/driver/profil/update/",

    "api/partner/login/", "api/partner/orders/", "api/partner/orders/{id}/",
    "api/partner/orders/{id}/refuse/", "api/partner/orders/{id}/update-status/",
    "api/partner/orders/{id}/photo/", "api/partner/orders/{id}/status/",

    "api/ops/login/", "api/ops/dashboard/", "api/ops/orders/{id}/",
    "api/ops/orders/{id}/status/", "api/ops/orders/{id}/litige-status/",
    "api/ops/orders/{id}/assign/", "api/ops/orders/{id}/assign-driver/",
    "api/ops/orders/{id}/assign-return-driver/", "api/ops/suggest-pressing/{id}/",
    "api/ops/suggest-driver/{id}/", "api/ops/orders/{id}/mark-paid/",
    "api/ops/paiements/", "api/ops/paiements/enregistrer/", "api/ops/wallets/",
    "api/ops/score/pressing/{id}/", "api/ops/score/livreur/{id}/",
    "api/ops/rapport/hebdo/", "api/ops/activite-jour/",

    "api/config/", "api/health/", "api/fcm/token/",
}


def normalize_pattern(pattern_str):
    """Remplace les groupes nommes Django (<int:order_id>, etc.) par {id}."""
    import re
    normalized = re.sub(r"<[^>]+>", "{id}", pattern_str)
    return normalized.lstrip("/")


def collect_routes(resolver, prefix=""):
    routes = []
    for pattern in resolver.url_patterns:
        pattern_str = str(pattern.pattern)
        full = prefix + pattern_str
        if hasattr(pattern, "url_patterns"):
            routes.extend(collect_routes(pattern, full))
        else:
            if "api/" in full:
                routes.append(normalize_pattern(full))
    return routes


class Command(BaseCommand):
    help = "Verifie la synchronisation entre le FOS-202 (API Catalogue) et les routes Django reelles"

    def handle(self, *args, **options):
        resolver = get_resolver()
        real_routes = set(collect_routes(resolver))

        undocumented = sorted(real_routes - DOCUMENTED_ROUTES)
        stale = sorted(DOCUMENTED_ROUTES - real_routes)

        self.stdout.write(f"Total routes reelles detectees (contenant 'api/'): {len(real_routes)}")
        self.stdout.write(f"Total routes documentees (FOS-202): {len(DOCUMENTED_ROUTES)}")

        if undocumented:
            self.stdout.write(self.style.WARNING(f"\nRoutes du CODE absentes du FOS-202 ({len(undocumented)}):"))
            for r in undocumented:
                self.stdout.write(f"  - {r}")
        if stale:
            self.stdout.write(self.style.ERROR(f"\nRoutes du FOS-202 absentes du CODE ({len(stale)}) - documentation perimee:"))
            for r in stale:
                self.stdout.write(f"  - {r}")

        if not undocumented and not stale:
            self.stdout.write(self.style.SUCCESS("\nFOS-202 parfaitement synchronise avec les routes reelles."))
        else:
            self.stdout.write(self.style.WARNING("\nFOS-202 necessite une mise a jour (voir details ci-dessus)."))
