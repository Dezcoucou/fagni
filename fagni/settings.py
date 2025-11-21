import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'changeme-dev-key')
DEBUG = True

ADMINS = [("Admin", "admin@example.com")]
ALLOWED_HOSTS = ['dezcoucou80.pythonanywhere.com', '127.0.0.1', 'localhost']
LOGIN_URL = '/admin/login/'
CSRF_TRUSTED_ORIGINS = ['https://dezcoucou80.pythonanywhere.com']

INSTALLED_APPS = [
    'dashboard',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'orders',
    'partners',
    'django_extensions',
    'mlm',
]


MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
        'django.middleware.locale.LocaleMiddleware',
'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'fagni.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / "templates",        # 👈 templates/ (accueil.html, admin/, etc.)
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = 'fr'
TIME_ZONE = 'Africa/Abidjan'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_L10N = True

LOCALE_PATHS = [ BASE_DIR / 'locale' ]
LOGIN_URL = '/admin/login/'
LOGIN_REDIRECT_URL = '/orders/new-multi/'

LANGUAGES = [
    ('fr', 'Français'),
]

LOCALE_PATHS = [ BASE_DIR / 'locale' ]
# --------------------[ Localisation FR – auto ]--------------------
LANGUAGE_CODE = 'fr'
LANGUAGES = [("fr", "Français")]
TIME_ZONE = 'Africa/Abidjan'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Dossier templates si pas déjà configuré
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
try:
    TEMPLATES[0]["DIRS"] = list(set(TEMPLATES[0].get("DIRS", []) + [str(BASE_DIR / "templates")]))
except Exception:
    pass
# ------------------------------------------------------------------


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "file": {
            "class": "logging.FileHandler",
            "filename": str(BASE_DIR / "logs" / "app.log"),
            "level": "INFO",
        },
        "mail_admins": {
            "class": "django.utils.log.AdminEmailHandler",
            "level": "ERROR",
        },
    },
    "loggers": {
        "django.request": {"handlers": ["file","mail_admins"], "level": "ERROR", "propagate": True},
        "orders": {"handlers": ["file"], "level": "INFO"},
    },
}


# ---- Sécurisation production ----
SECURE_HSTS_SECONDS = 31536000
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# ========================
# FAGNI – LOGISTIQUE / LIVREURS
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
    "client_min_fee": 1000,   # minimum facturé au client
    "client_max_fee": 5000,   # plafond sécurité
    "client_price_per_km": 100,  # prix de base client par km AR
    "client_fixed_fee": 300,     # fixe client

    # ⚡ PAIEMENT LIVREUR (base, hors surge)
    "driver_price_per_km": 75,   # rémunération livreur par km AR

    # ⚡ OBJECTIF MARGE FAGNI
    "fagni_margin_per_km": 100,  # marge cible par km AR
    "fagni_min_margin": 300,     # marge minimale cible par course

    # ⚡ SURGE / MAJORATION DYNAMIQUE
    "peak_multiplier": 1.3,      # heures de pointe (7–10 / 17–20)
    "night_multiplier": 1.4,     # nuit (20h–6h)
    "rain_multiplier": 1.3,      # pluie normale (placeholder)
    "heavy_rain_multiplier": 1.6,# forte pluie / orage (placeholder)

    # Part de la majoration (surge) pour le livreur
    "driver_surge_share": 0.6,   # 60% du supplément pour le livreur
}

# Clé Distance Matrix côté serveur
# (tu peux utiliser la même que GOOGLE_MAPS_API_KEY si elle a les droits)
GOOGLE_DISTANCE_MATRIX_API_KEY = os.getenv(
    "GOOGLE_DISTANCE_MATRIX_API_KEY",
    os.getenv("GOOGLE_MAPS_API_KEY", "")
)
# --- HTTPS redirect configurable ---
SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', '0') == '1'
# Clé d'API Google Maps pour FAGNI
GOOGLE_MAPS_API_KEY = "AIzaSyAq_L3zzJ95y5bUyGZzm2Iq7_gXsWYcP-0"

# ==== FAGNI DELIVERY PRICING ====
from decimal import Decimal

#DELIVERY_MIN_FEE = Decimal("2000")       # minimum facturé au client
#DELIVERY_PRICE_PER_KM = Decimal("250")   # prix client par km
#DRIVER_COST_PER_KM = Decimal("150")      # coût interne par km (carburant, usure, etc.)

# Clé API Google Distance Matrix (à mettre dans ton vrai fichier local / env si tu préfères)
GOOGLE_MAPS_API_KEY = "AIzaSyAq_L3zzJ95y5bUyGZzm2Iq7_gXsWYcP-0"
