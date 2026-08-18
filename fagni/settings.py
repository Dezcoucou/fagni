import os
import dj_database_url
from pathlib import Path as _Path
from dotenv import load_dotenv
load_dotenv(_Path(__file__).parent.parent / '.env')
from pathlib import Path
from decimal import Decimal

import sys
# "test" in sys.argv detecte `manage.py test`, mais pas `pytest` (sys.argv
# vaut alors ['pytest', ...] ou ['-q', ...] selon l'invocation) - la CI de ce
# depot lance `pytest -q`, ce qui laissait TESTING=False et donc
# SECURE_SSL_REDIRECT actif (voir plus bas), redirigeant en 301 chaque
# requete du client de test Django et faisant echouer toute la suite sous
# pytest independamment du contenu des tests eux-memes.
TESTING = "test" in sys.argv or "pytest" in sys.modules

BASE_DIR = Path(__file__).resolve().parent.parent

# ========================
#  CONFIG DE BASE
# ========================
SECRET_KEY = (os.getenv("SECRET_KEY") or "").strip()
if not SECRET_KEY:
    if TESTING:
        SECRET_KEY = "django-test-only-secret-key-not-for-production"
    else:
        raise RuntimeError("SECRET_KEY environment variable is required")
DEBUG = False

CLIENT_SESSION_KEY = "fagni_client_phone"

ADMINS = [("Admin", "admin@example.com")]
# --- Hosts autorisés ---
# DEV par défaut, mais en prod Render on pilote via ALLOWED_HOSTS="a.com,b.onrender.com"
ALLOWED_HOSTS = [
    'dezcoucou80.pythonanywhere.com',
    '127.0.0.1',
    'localhost',
    '0.0.0.0',
]
# Ajouter hosts depuis variable d'environnement
_env_hosts = (os.getenv("ALLOWED_HOSTS", "") or "").strip()
if _env_hosts:
    ALLOWED_HOSTS += [h.strip() for h in _env_hosts.split(",") if h.strip()]

# Ajoute automatiquement le host de SITE_BASE_URL (si défini)
from urllib.parse import urlparse

# Base URL dynamique (local vs production)
SITE_BASE_URL = (os.getenv("SITE_BASE_URL", "http://127.0.0.1:8000") or "").strip().rstrip("/")

try:
    _u = urlparse((os.getenv("SITE_BASE_URL", "") or "").strip())
    _host = (_u.hostname or "").strip()
    if _host and _host not in ALLOWED_HOSTS and ALLOWED_HOSTS != ["*"]:
        ALLOWED_HOSTS.append(_host)
except Exception:
    import logging
    logging.getLogger("fagni.fagni.settings").exception("Exception silencieuse (auto-log) - fichier=fagni/settings.py ligne=47")

# Override via env (Render/Prod)
_env_hosts = (os.getenv("ALLOWED_HOSTS", "") or "").strip()
if _env_hosts:
    ALLOWED_HOSTS = [h.strip() for h in _env_hosts.split(",") if h.strip()]

# Optionnel: si tu as besoin de tester depuis d'autres IP du LAN/VPN
# ALLOWED_HOSTS += ["192.168.0.0/16"]  # (Django ne supporte pas les CIDR ici)

# Si tu veux une règle "dev only" (moins strict) :
if os.environ.get("DJANGO_DEBUG_ALLOW_ALL_HOSTS") == "1":
    ALLOWED_HOSTS = ["*"]

CSRF_TRUSTED_ORIGINS = [
    "https://fagni-client.vercel.app",
    "https://fagni-ops.vercel.app",
    "https://fagni-driver.vercel.app",
    "https://fagni-partner.vercel.app",
    "https://fagni-api.onrender.com",
] + ([SITE_BASE_URL] if SITE_BASE_URL.startswith("https://") else [])
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
    'partners',
    'django_extensions',
    'mlm',
    'api',
    'portal',
    'wallets.apps.WalletsConfig',
    'bonuses',
    'core',
    'accounts',
    'services',
    'orders',
    'rest_framework',
    'corsheaders',
    'logistics',
    'production',
    'pricing',
    'payments',
    'tracking',
]

# ========================
#  MIDDLEWARE
# ========================
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
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

if TESTING:
    # Les tests doivent être totalement isolés de la base de production,
    # même lorsqu'un DATABASE_URL est chargé depuis .env.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }
elif DATABASE_URL:
    # PythonAnywhere / Prod (MySQL) ou override local via DATABASE_URL
    is_sqlite_url = DATABASE_URL.startswith("sqlite:")
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=0,
            ssl_require=(
                not is_sqlite_url
                and os.getenv("DB_SSL_REQUIRE", "1") == "1"
            ),
        )
    }

    # Active MySQL Strict Mode uniquement pour MySQL.
    # Ces options ne sont pas valides avec PostgreSQL/Psycopg.
    engine = DATABASES["default"].get("ENGINE", "")

    if engine.endswith("mysql"):
        DATABASES["default"].setdefault("OPTIONS", {})
        DATABASES["default"]["OPTIONS"]["init_command"] = (
            "SET sql_mode='STRICT_TRANS_TABLES', NAMES 'utf8mb4'"
        )
        DATABASES["default"]["OPTIONS"]["charset"] = "utf8mb4"
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
# --- LOT_2_16_SESSIONS_LOGGER_OK ---

# --- LOT_2_16B_SESSIONS_LOGGER_HARD_MUTE_OK ---
# Coupe définitivement le warning "Session data corrupted" en DEV.
if DEBUG:
    for _lg in (
        "django.contrib.sessions",
        "django.contrib.sessions.backends",
        "django.contrib.sessions.backends.base",
        "django.contrib.sessions.backends.db",
    ):
        LOGGING["loggers"].setdefault(_lg, {})
        LOGGING["loggers"][_lg].update({
            "handlers": [],        # pas de handler => rien n'est émis
            "level": "CRITICAL",   # au cas où un handler serait ajouté ailleurs
            "propagate": False,    # n'envoie rien au root logger
        })

# Réduit le bruit "Session data corrupted" en DEV (warning émis par le backend sessions)
if DEBUG:
    LOGGING.setdefault("loggers", {})
    LOGGING["loggers"].setdefault("django.contrib.sessions", {})
    LOGGING["loggers"]["django.contrib.sessions"].update({
        "handlers": ["console"],
        "level": "ERROR",
        "propagate": False,
    })
    # Certains messages viennent aussi de sous-loggers/backends
    LOGGING["loggers"].setdefault("django.contrib.sessions.backends", {})
    LOGGING["loggers"]["django.contrib.sessions.backends"].update({
        "handlers": ["console"],
        "level": "ERROR",
        "propagate": False,
    })

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

# Sprint P0, Wave 1 (BP2) : reserve l'inscription/connexion du pilote aux
# numeros presents dans PilotWhitelist (orders/models.py). Reste a False
# tant que la liste n'a pas ete verifiee et peuplee - voir les management
# commands audit_pilot_whitelist_candidates et populate_pilot_whitelist.
# Plan de sortie (OTP reel) : repasser a False, aucun autre changement requis.
PILOT_WHITELIST_ENFORCED = (os.getenv("PILOT_WHITELIST_ENFORCED", "false") or "").strip().lower() in ("1", "true", "yes", "on")

# Sprint P0, Wave 3 (BC1) : quand actif, api_create_order tente une
# auto-affectation (pressing + livreur de collecte uniquement, jamais le
# livreur retour) juste apres la creation de la commande fagni-client.
# Desactive par defaut : comportement strictement identique a avant BC1.
AUTO_ASSIGN_ON_CLIENT_ORDER = (os.getenv("AUTO_ASSIGN_ON_CLIENT_ORDER", "false") or "").strip().lower() in ("1", "true", "yes", "on")

# BC3 : quand actif, partner_update_status tente une auto-affectation du
# livreur retour au moment ou le pressing marque la commande PRET (meme
# moteur pick_best_driver que BC1 collecte et que ops_assign_return_driver).
# Desactive par defaut : comportement strictement identique a avant BC3
# (mission retour creee mais jamais assignee automatiquement).
AUTO_ASSIGN_RETURN_DRIVER = (os.getenv("AUTO_ASSIGN_RETURN_DRIVER", "false") or "").strip().lower() in ("1", "true", "yes", "on")

TWILIO_ACCOUNT_SID = (os.getenv("TWILIO_ACCOUNT_SID", "") or "").strip()
TWILIO_AUTH_TOKEN = (os.getenv("TWILIO_AUTH_TOKEN", "") or "").strip()
TWILIO_VERIFY_SERVICE_SID = (os.getenv("TWILIO_VERIFY_SERVICE_SID", "") or "").strip()
TWILIO_VERIFY_ENABLED = (os.getenv("TWILIO_VERIFY_ENABLED", "false") or "").strip().lower() in ("1", "true", "yes", "on")
TWILIO_WHATSAPP_FROM = (os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886") or "").strip()
TWILIO_WHATSAPP_CONTENT_SID = (os.getenv("TWILIO_WHATSAPP_CONTENT_SID", "") or "").strip()
TWILIO_WHATSAPP_ENABLED = (os.getenv("TWILIO_WHATSAPP_ENABLED", "false") or "").strip().lower() in ("1", "true", "yes", "on")

TWILIO_ACCOUNT_SID = (os.getenv("TWILIO_ACCOUNT_SID", "") or "").strip()
TWILIO_AUTH_TOKEN = (os.getenv("TWILIO_AUTH_TOKEN", "") or "").strip()
TWILIO_VERIFY_SERVICE_SID = (os.getenv("TWILIO_VERIFY_SERVICE_SID", "") or "").strip()
TWILIO_VERIFY_ENABLED = (os.getenv("TWILIO_VERIFY_ENABLED", "false") or "").strip().lower() in ("1", "true", "yes", "on")

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

WHATSAPP_PHONE_NUMBER_ID = (os.getenv("WHATSAPP_PHONE_NUMBER_ID", "") or "").strip()
WHATSAPP_ACCESS_TOKEN = (os.getenv("WHATSAPP_ACCESS_TOKEN", "") or "").strip()

# Lien marchand Wave (ex: https://pay.wave.com/m/.../c/ci/)
WAVE_MERCHANT_LINK_BASE = "https://pay.wave.com/m/M_ci_8SO-R9nJg71k/c/ci/"
# ========================
#  SECURITY (PROD)
# ========================
if not DEBUG and not TESTING:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True

    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# --- DEV: disable template cached loader (avoid stale templates / blank pages) ---
try:
    if DEBUG:
        opts = TEMPLATES[0].setdefault("OPTIONS", {})
        loaders = opts.get("loaders")
        # si loaders est défini et contient cached.Loader, on repasse en loaders standards
        if loaders:
            opts["loaders"] = [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ]
except Exception:
    import logging
    logging.getLogger("fagni.fagni.settings").exception("Exception silencieuse (auto-log) - fichier=fagni/settings.py ligne=394")


# ========================
#  UNFOLD ADMIN
# ========================
UNFOLD = {
    "SITE_TITLE": "FAGNI Admin",
    "SITE_HEADER": "FAGNI",
    "SITE_SUBHEADER": "Pressing • Collecte • Livraison",
    "SITE_URL": "/",
    "SITE_SYMBOL": "local_laundry_service",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "COLORS": {
        "primary": {
            "50": "255 248 240",
            "100": "255 237 213",
            "200": "254 215 170",
            "300": "253 186 116",
            "400": "251 146 60",
            "500": "232 119 34",
            "600": "194 65 12",
            "700": "154 52 18",
            "800": "124 45 18",
            "900": "101 35 13",
            "950": "67 20 7",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Commandes",
                "separator": True,
                "items": [
                    {
                        "title": "Toutes les commandes",
                        "icon": "receipt_long",
                        "link": "/admin/orders/order/",
                        "badge": "orders.admin.order_count",
                    },
                    {
                        "title": "Clients",
                        "icon": "person",
                        "link": "/admin/orders/customer/",
                    },
                    {
                        "title": "Paiements",
                        "icon": "payments",
                        "link": "/admin/orders/payment/",
                    },
                ],
            },
            {
                "title": "Partenaires",
                "separator": True,
                "items": [
                    {
                        "title": "Blanchisseries",
                        "icon": "local_laundry_service",
                        "link": "/admin/partners/laundrypartner/",
                    },
                    {
                        "title": "Livreurs",
                        "icon": "delivery_dining",
                        "link": "/admin/partners/deliverypartner/",
                    },
                ],
            },
            {
                "title": "Finance",
                "separator": True,
                "items": [
                    {
                        "title": "Wallets",
                        "icon": "account_balance_wallet",
                        "link": "/admin/wallets/wallet/",
                    },
                    {
                        "title": "Retraits",
                        "icon": "currency_exchange",
                        "link": "/admin/wallets/withdrawalrequest/",
                    },
                ],
            },
            {
                "title": "Parrainage",
                "separator": True,
                "items": [
                    {
                        "title": "Affiliés",
                        "icon": "group_add",
                        "link": "/admin/mlm/",
                    },
                ],
            },
        ],
    },
}

WAVE_CHECKOUT_SIGNING_SECRET = os.getenv("WAVE_CHECKOUT_SIGNING_SECRET", "")
WAVE_WEBHOOK_SIGNING_SECRET = os.getenv("WAVE_WEBHOOK_SIGNING_SECRET", "")
# ============================================================
# Security loggers — wallet/payment guards
# ============================================================
try:
    LOGGING.setdefault("loggers", {})
    LOGGING["loggers"].setdefault("wallet_security", {
        "handlers": ["console"],
        "level": "ERROR",
        "propagate": False,
    })
    LOGGING["loggers"].setdefault("payment_security", {
        "handlers": ["console"],
        "level": "ERROR",
        "propagate": False,
    })
except Exception:
    import logging
    logging.getLogger("fagni.fagni.settings").exception("Exception silencieuse (auto-log) - fichier=fagni/settings.py ligne=514")

# ── API CLIENT FAGNI ─────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
}

CORS_ALLOWED_ORIGINS = [
    "https://fagni-client.vercel.app",
    "https://fagni-driver.vercel.app",
    "https://fagni-partner.vercel.app",
    "https://fagni-ops.vercel.app",
]
CORS_ALLOW_CREDENTIALS = True

OPS_PASSWORD = (os.getenv('OPS_PASSWORD') or '').strip()
SIMULATEUR_NOTIFY_KEY = (os.getenv('SIMULATEUR_NOTIFY_KEY') or '').strip()

DEFAULT_ADMIN_USERNAME = (os.getenv('DEFAULT_ADMIN_USERNAME') or 'admin').strip()
DEFAULT_ADMIN_EMAIL = (os.getenv('DEFAULT_ADMIN_EMAIL') or 'admin@fagni.com').strip()
DEFAULT_ADMIN_PASSWORD = (os.getenv('DEFAULT_ADMIN_PASSWORD') or '').strip()

# Cloudinary
import cloudinary
cloudinary.config(
    cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME', ''),
    api_key    = os.getenv('CLOUDINARY_API_KEY', ''),
    api_secret = os.getenv('CLOUDINARY_API_SECRET', ''),
    secure     = True
)

SECURE_HSTS_SECONDS = 31536000
