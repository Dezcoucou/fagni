import os
import dj_database_url
from pathlib import Path
from decimal import Decimal

import sys
TESTING = "test" in sys.argv

BASE_DIR = Path(__file__).resolve().parent.parent

# ========================
#  CONFIG DE BASE
# ========================
SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-change-me")
DEBUG = (os.getenv("DEBUG", "1").strip() in ("1","true","True","yes","on"))  # 1=dev, 0=prod

CLIENT_SESSION_KEY = "fagni_client_phone"

ADMINS = [("Admin", "admin@example.com")]
# --- Hosts autorisés ---
# DEV par défaut, mais en prod Render on pilote via ALLOWED_HOSTS="a.com,b.onrender.com"
ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "0.0.0.0",
    "100.115.92.198",  # ton IP actuelle (Chromebook)
    "dezcoucou80.pythonanywhere.com",
    "fagni-t1s8.onrender.com",
]

# Override via env (Render/Prod)
_env_hosts = (os.getenv("ALLOWED_HOSTS", "") or "").strip()
if _env_hosts:
    ALLOWED_HOSTS = [h.strip() for h in _env_hosts.split(",") if h.strip()]

# Ajoute automatiquement le host de SITE_BASE_URL (si défini)
try:
    from urllib.parse import urlparse
    _u = urlparse((os.getenv("SITE_BASE_URL", "") or "").strip())
    _host = (_u.hostname or "").strip()
    if _host and _host not in ALLOWED_HOSTS and ALLOWED_HOSTS != ["*"]:
        ALLOWED_HOSTS.append(_host)
except Exception:
    pass
# Optionnel: si tu as besoin de tester depuis d'autres IP du LAN/VPN
# ALLOWED_HOSTS += ["192.168.0.0/16"]  # (Django ne supporte pas les CIDR ici)

# Si tu veux une règle "dev only" (moins strict) :
if os.environ.get("DJANGO_DEBUG_ALLOW_ALL_HOSTS") == "1":
    ALLOWED_HOSTS = ["*"]
SITE_BASE_URL = (os.getenv("SITE_BASE_URL", "http://127.0.0.1:8000") or "").strip().rstrip("/")
CSRF_TRUSTED_ORIGINS = [SITE_BASE_URL] if SITE_BASE_URL.startswith("https://") else []
# Pour les QR codes FAGNI (tickets PDF)
FAGNI_QR_BASE_URL = SITE_BASE_URL
# ========================
#  APPS
# ========================
INSTALLED_APPS = [
    'dashboard',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    'orders.apps.OrdersConfig',  # ✅ IMPORTANT : PAS juste "orders"
    'partners',
    'django_extensions',
    'mlm',
    'api',
    'portal',
    'wallets.apps.WalletsConfig',
    'bonuses',
]

# ========================
#  MIDDLEWARE
# ========================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',   # ✅ une seule fois
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'fagni.urls'

# ========================
#  TEMPLATES
# ========================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / "templates",   # templates/ (accueil.html, etc.)
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'fagni.context_processors.google_keys',
            ],
        },
    },
]

# ========================
#  BASE DE DONNÉES
# ========================
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

if DATABASE_URL:
    # Render / Prod (PostgreSQL)
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.getenv("DB_CONN_MAX_AGE", "600")),
            ssl_require=(os.getenv("DB_SSL_REQUIRE", "1") == "1"),
        )
    }
else:
    # Local (SQLite)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = []

# ========================
#  LOCALISATION
# ========================
LANGUAGE_CODE = 'fr'
LANGUAGES = [
    ('fr', 'Français'),
]

TIME_ZONE = 'Africa/Abidjan'
USE_I18N = True
USE_L10N = True
USE_TZ = True

LOCALE_PATHS = [BASE_DIR / 'locale']

# ========================
#  STATIC & MEDIA
# ========================
STATIC_URL = '/static/'

# Dossier static principal du projet (CSS/JS/images globaux)
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

# Django >= 4.2/5.x: STORAGES remplace STATICFILES_STORAGE (deprecated)
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
}
# STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"  # (deprecated, remplacé par STORAGES)
WHITENOISE_SKIP_COMPRESS_EXTENSIONS = ('.map',)

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ========================
#  LOGGING
# ========================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": True,
        },
        "fagni": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

# ========================
#  SÉCURITÉ HTTPS
# ========================
# HSTS seulement si DEBUG = False
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# Redirection HTTPS pilotée par variable d'env
SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', '0') == '1'

# ========================
#  FAGNI – LOGISTIQUE / LIVREURS
# ========================
FAGNI_LOGISTICS = {
    # Carburant / moto
    "fuel_price": 875,                  # FCFA / litre
    "consumption_l_per_100km": 2.2,     # L / 100 km
    "idle_consumption_l_per_hour": 0.8, # L / heure de ralenti
    "wear_cost_per_km": 30,             # FCFA / km (usure)
    "time_cost_per_minute": 10,         # FCFA / minute (temps du livreur)

    # Prime fixe interne livreur (par jambe)
    "driver_fixed_per_leg": 300,        # FCFA par jambe (aller ou retour)

    # ⚡ PARAMÈTRES CLIENT (borne le prix final)
    "client_min_fee": 1000,      # minimum facturé au client
    "client_max_fee": 5000,      # plafond sécurité
    "client_price_per_km": 100,  # prix de base client par km AR
    "client_fixed_fee": 300,     # fixe client

    # ⚡ PAIEMENT LIVREUR (base, hors surge)
    "driver_price_per_km": 75,   # rémunération livreur par km AR

    # ⚡ OBJECTIF MARGE FAGNI
    "fagni_margin_per_km": 100,  # marge cible par km AR
    "fagni_min_margin": 300,     # marge minimale cible par course

    # ⚡ SURGE / MAJORATION DYNAMIQUE
    "peak_multiplier": 1.3,       # heures de pointe (7–10 / 17–20)
    "night_multiplier": 1.4,      # nuit (20h–6h)
    "rain_multiplier": 1.3,       # pluie normale (placeholder)
    "heavy_rain_multiplier": 1.6, # forte pluie / orage (placeholder)

    # Part de la majoration (surge) pour le livreur
    "driver_surge_share": 0.6,   # 60% du supplément pour le livreur
}

# ========================
#  GOOGLE MAPS / DISTANCE
# ========================
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GOOGLE_DISTANCE_MATRIX_API_KEY = os.getenv("GOOGLE_DISTANCE_MATRIX_API_KEY", "")

# ==========================
# Auth / redirections FAGNI
# ==========================
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# === EMAILS EN DEV : AFFICHAGE EN CONSOLE ===
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "no-reply@fagni.local"

# === GEO CODING (Lot 2.3) ===
NOMINATIM_USER_AGENT = "FAGNI/1.0 (Côte d'Ivoire) contact: support@fagni.local"
GEOCODING_COUNTRY_CODES = "ci"

# -------------------------------------------------------------------
# Wave (Checkout + lien marchand)
# -------------------------------------------------------------------
WAVE_CHECKOUT_ENABLED = (os.getenv("WAVE_CHECKOUT_ENABLED", "") or "").strip().lower() in ("1","true","yes","on")
WAVE_CHECKOUT_API_KEY = (os.getenv("WAVE_CHECKOUT_API_KEY", "") or "").strip()
WAVE_RECEIVER_PHONE   = (os.getenv("WAVE_RECEIVER_PHONE", "") or "").strip()

# Lien marchand Wave (ex: https://pay.wave.com/m/.../c/ci/)
WAVE_MERCHANT_LINK_BASE = (os.getenv("WAVE_MERCHANT_LINK_BASE", "") or "").strip()
# ========================
#  SECURITY (PROD)
# ========================
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True

    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
