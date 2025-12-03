import os
from pathlib import Path
from decimal import Decimal

BASE_DIR = Path(__file__).resolve().parent.parent

# ========================
#  CONFIG DE BASE
# ========================
SECRET_KEY = os.getenv('SECRET_KEY', 'changeme-dev-key')
DEBUG = True  # ⚠️ à mettre à False en prod

ADMINS = [("Admin", "admin@example.com")]

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "dezcoucou80.pythonanywhere.com",  # tu peux le laisser si tu utilises encore PythonAnywhere
    "fagni-t1s8.onrender.com",
]

CSRF_TRUSTED_ORIGINS = [
    "https://dezcoucou80.pythonanywhere.com",
    "https://fagni-t1s8.onrender.com",
]

SITE_BASE_URL = "https://fagni-t1s8.onrender.com"

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
    'wallets',
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
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

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
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
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
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "AIzaSyANSUv_OvcDO9aH5ScY2QwR5BsubHbjghU")
GOOGLE_DISTANCE_MATRIX_API_KEY = os.getenv("GOOGLE_DISTANCE_MATRIX_API_KEY", "AIzaSyANSUv_OvcDO9aH5ScY2QwR5BsubHbjghU")

# ==========================
# Auth / redirections FAGNI
# ==========================
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/orders/driver-app/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# === EMAILS EN DEV : AFFICHAGE EN CONSOLE ===
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "no-reply@fagni.local"
