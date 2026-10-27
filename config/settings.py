"""Django settings for PhotoPholio."""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Security / environment -------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-in-production")
DEBUG = os.environ.get("DEBUG", "True") == "True"
ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]

# Render.com sets this automatically
RENDER_HOST = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if RENDER_HOST:
    ALLOWED_HOSTS.append(RENDER_HOST)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_HOST}")

# --- Apps --------------------------------------------------------------------
INSTALLED_APPS = [
    "jazzmin",  # admin theme (must be before django.contrib.admin)
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "photopholio",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves static files in production
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ----------------------------------------------------------------
# SQLite by default; set DATABASE_URL to use PostgreSQL in production.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=600
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# --- Static & media ----------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
# Uploaded photos. On free hosts (Render) the server's disk is wiped on every deploy and restart, so photos would vanish.
# Set CLOUDINARY_URL (from your Cloudinary dashboard, in the host's Environment settings only, never in code) and every upload
# goes to Cloudinary instead and stays forever. Without it, photos use the local media/ folder as before.
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "")
if CLOUDINARY_URL:
    INSTALLED_APPS += ["cloudinary_storage", "cloudinary"]
    STORAGES["default"] = {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Email ----------------------------------------------------------------------
# With no email settings, emails are printed in the terminal running the server (nothing to set up).
# For real emails set these environment variables (Gmail needs an "App Password", not your normal password):
#   EMAIL_HOST_USER, EMAIL_HOST_PASSWORD   and   SITE_URL (the address used in links inside emails)
# The address used in links inside emails. If SITE_URL is not set, a Render deployment falls back to the address Render itself
# provides (RENDER_EXTERNAL_URL), so email buttons never point at 127.0.0.1 by mistake.
SITE_URL = (os.environ.get("SITE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://127.0.0.1:8000").rstrip("/")
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
# Free hosts like Render block SMTP, so there you set BREVO_API_KEY + BREVO_SENDER_EMAIL instead (email goes over HTTPS).
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL", "")  # must be a sender you verified inside Brevo
if BREVO_API_KEY and BREVO_SENDER_EMAIL:
    EMAIL_BACKEND = "photopholio.email_backends.BrevoEmailBackend"
elif EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
    EMAIL_TIMEOUT = 10  # never let a slow mail server hang a page
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# How PhotoPholio earns: this percent of the photographer's SERVICE price (never of travel) is kept as commission.
# The customer pays no extra fee. A single photographer can have their own percent in the admin.
PLATFORM_COMMISSION_PERCENT = int(os.environ.get("PLATFORM_COMMISSION_PERCENT", "10"))
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", f"PhotoPholio <{BREVO_SENDER_EMAIL or EMAIL_HOST_USER or 'no-reply@photopholio.local'}>")

# --- Auth --------------------------------------------------------------------
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

# --- Production hardening ------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# --- Jazzmin (admin theme) -----------------------------------------------------
JAZZMIN_SETTINGS = {
    "site_title": "PhotoPholio Admin",
    "site_header": "PhotoPholio",
    "site_brand": "PhotoPholio",
    "welcome_sign": "PhotoPholio control room",
    "copyright": "PhotoPholio",
    "search_model": ["photopholio.Photographer", "photopholio.Booking"],
    "icons": {
        "auth.User": "fas fa-user",
        "photopholio.Photographer": "fas fa-camera",
        "photopholio.Portfolio": "fas fa-folder-open",
        "photopholio.Photo": "fas fa-image",
        "photopholio.Booking": "fas fa-calendar-check",
        "photopholio.Payment": "fas fa-credit-card",
        "photopholio.Feedback": "fas fa-star",
        "photopholio.ContactUs": "fas fa-envelope",
        "photopholio.Country": "fas fa-globe",
        "photopholio.State": "fas fa-map",
        "photopholio.City": "fas fa-city",
        "photopholio.UserProfile": "fas fa-id-card",
    },
    "topmenu_links": [
        {"name": "Home", "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "Analytics", "url": "admin:photopholio_analytics", "permissions": ["auth.view_user"]},
        {"name": "Payouts", "url": "admin:photopholio_payouts", "permissions": ["auth.view_user"]},
        {"name": "Disputes", "url": "admin:photopholio_disputes", "permissions": ["auth.view_user"]},
    ],
    "custom_links": {
        "photopholio": [
            {"name": "Analytics", "url": "admin:photopholio_analytics", "icon": "fas fa-chart-line",
             "permissions": ["auth.view_user"]},
            {"name": "Payouts", "url": "admin:photopholio_payouts", "icon": "fas fa-money-bill-wave",
             "permissions": ["auth.view_user"]},
            {"name": "Disputes", "url": "admin:photopholio_disputes", "icon": "fas fa-gavel",
             "permissions": ["auth.view_user"]},
        ]
    },
}
