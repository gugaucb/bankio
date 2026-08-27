"""
Django settings for Bankio - a digital banking modular monolith.
"""
import os
from pathlib import Path

import dj_database_url

from config.env_utils import env_bool, env_list, env_str, secret_or_file

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY: in production (DEBUG=false) an absent or known-insecure SECRET_KEY
# is a hard failure — we never silently fall back to a shared dev key.
DEBUG = env_bool("DJANGO_DEBUG", False)
_INSECURE_DEV_KEY = "dev-only-secret-key-change-me"
try:
    SECRET_KEY = secret_or_file("DJANGO_SECRET_KEY") if not DEBUG else None
except ValueError:
    SECRET_KEY = None
if SECRET_KEY is None:
    if not DEBUG:
        raise RuntimeError(
            "DJANGO_SECRET_KEY is required when DJANGO_DEBUG=false "
            "(set DJANGO_SECRET_KEY or DJANGO_SECRET_KEY_FILE)"
        )
    SECRET_KEY = _INSECURE_DEV_KEY
elif SECRET_KEY == _INSECURE_DEV_KEY and not DEBUG:
    raise RuntimeError("Refusing to start production with the insecure development SECRET_KEY")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])
for _origin in CSRF_TRUSTED_ORIGINS:
    if not _origin.startswith(("http://", "https://")):
        raise RuntimeError(f"CSRF_TRUSTED_ORIGINS entries must include scheme: {_origin!r}")

TIME_ZONE = env_str("DJANGO_TIME_ZONE", "UTC")

# Secure cookies are opt-out so localhost HTTP keeps working; production behind
# HTTPS should set BANKIO_SECURE_COOKIES=true (documented in .env.example).
_SECURE_COOKIES = env_bool("BANKIO_SECURE_COOKIES", not DEBUG)
SESSION_COOKIE_SECURE = _SECURE_COOKIES
CSRF_COOKIE_SECURE = _SECURE_COOKIES
if _SECURE_COOKIES:
    SECURE_SSL_REDIRECT = env_bool("BANKIO_SSL_REDIRECT", False)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

LOCAL_APPS = [
    "apps.identity",
    "apps.customers",
    "apps.accounts",
    "apps.ledger",
    "apps.transfers",
    "apps.cards",
    "apps.payments",
    "apps.lending",
    "apps.investments",
    "apps.compliance",
    "apps.fraud",
    "apps.notifications",
    "apps.support",
    "apps.audit",
    "apps.managerops",
    "apps.portal",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves STATIC_ROOT under gunicorn (no runserver in prod)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.audit.middleware.AuditMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.notifications.context_processors.unread_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL")
        # Local-development fallback only; docker compose always supplies a
        # real DATABASE_URL built from POSTGRES_* variables.
        or "postgres://bankio:bankio_dev_password@localhost:5434/bankio",
        conn_max_age=60,
    )
}

AUTH_USER_MODEL = "identity.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

SESSION_COOKIE_AGE = 3600  # 1h session expiration
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
CSRF_COOKIE_SAMESITE = "Lax"

# Banking limits
BANKING_DEFAULT_TX_LIMIT = "5000.00"
BANKING_DEFAULT_DAILY_LIMIT = "10000.00"

# Ledger anchoring (external proof layer; never blocks banking transactions)
LEDGER_ANCHOR_PROVIDER = "simulated"  # simulated | external
LEDGER_ANCHOR_FREQUENCY = "every_seal"  # every_seal | hourly | daily
LEDGER_ANCHOR_MIN_CONFIRMATIONS = 1  # provider polls before CONFIRMED

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
