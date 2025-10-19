import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'changeme-dev-key')
DEBUG = False

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
    'orders',
    'django_extensions',
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
        'DIRS': [BASE_DIR / 'templates'],
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

WSGI_APPLICATION = 'fagni.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
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
MEDIA_ROOT = BASE_DIR / 'media'

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

# --- HTTPS redirect configurable ---
import os
SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', '0') == '1'
