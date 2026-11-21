"""Local Django settings for KazanCourts."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY before running Django.")

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [host.strip() for host in os.environ.get(
    "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if host.strip()]
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "bookings.apps.BookingsConfig",
]

MIDDLEWARE = [
    "config.telemetry.TraceMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
            ]
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ["DB_HOST"],
        "PORT": os.environ["DB_PORT"],
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "TEST": {"NAME": "test_kazan_courts"},
        "OPTIONS": {"connect_timeout": 2},
    }
}

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

# One synthetic Kazan point for the shared outdoor-court policy.
# Keep live weather off until the user authorizes a real forecast request.
WEATHER_NETWORK_ENABLED = os.environ.get("WEATHER_NETWORK_ENABLED", "0") == "1"
WEATHER_LATITUDE = "55.793000"
WEATHER_LONGITUDE = "49.123000"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]


LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"json": {"()": "config.telemetry.JSONFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "kafka": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}
