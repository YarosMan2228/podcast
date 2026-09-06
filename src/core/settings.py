"""Django settings for podcast-pack.

Single-file, hackathon-friendly. Reads everything from env vars with sane
dev defaults. Import path: `core.settings` (see manage.py — `src/` is on
sys.path).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # project root
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


_DEV_SECRET = "dev-secret-change-me"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", _DEV_SECRET)
DEBUG = _env_bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

# Refuse to boot a non-debug deployment with the dev secret — a leaked
# default SECRET_KEY lets anyone forge signed cookies.
if not DEBUG and SECRET_KEY == _DEV_SECRET:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a real value when DJANGO_DEBUG=0"
    )

# Optional shared secret gating /api/ and /media/ (api.middleware). Empty =
# open, which is only acceptable on localhost.
APP_ACCESS_TOKEN = os.environ.get("APP_ACCESS_TOKEN", "").strip()
# Multi-user mode (services.access): per-user keys from the access_keys
# table, jobs scoped to their owner; APP_ACCESS_TOKEN becomes the master key.
APP_MULTI_USER = _env_bool("APP_MULTI_USER", False)

# Per-IP throttles (api.throttles). DRF rate syntax: "<n>/<sec|min|hour|day>".
API_RATE_LIMIT = os.environ.get("API_RATE_LIMIT", "300/min")
UPLOAD_RATE_LIMIT = os.environ.get("UPLOAD_RATE_LIMIT", "20/hour")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "api.apps.ApiConfig",
    "jobs.apps.JobsConfig",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "api.middleware.AccessTokenMiddleware",
]

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
# Behind a TLS-terminating proxy set DJANGO_SECURE_PROXY_SSL_HEADER=1 so
# request.is_secure() is true and the access cookie gets the Secure flag.
if _env_bool("DJANGO_SECURE_PROXY_SSL_HEADER", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

ROOT_URLCONF = "core.urls"
WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "podcastpack"),
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"

MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media"))
MEDIA_URL = "/media/"
ARTIFACTS_ROOT = os.environ.get("ARTIFACTS_ROOT", str(Path(MEDIA_ROOT) / "artifacts"))

MAX_UPLOAD_SIZE_MB = _env_int("MAX_UPLOAD_SIZE_MB", 500)
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE_BYTES
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # stream to disk past 10MB

MAX_EPISODE_DURATION_MIN = _env_int("MAX_EPISODE_DURATION_MIN", 180)

# External APIs
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1")
# Empty = OpenAI default. Set to e.g. https://api.groq.com/openai/v1 to use
# Groq as a free drop-in replacement (its API is OpenAI-compatible).
WHISPER_BASE_URL = os.environ.get("WHISPER_BASE_URL", "")
# Comma-separated ISO-639-1 codes the pipeline accepts; empty = any language
# Whisper detects (Claude is then told to write in that language).
TRANSCRIPTION_ALLOWED_LANGUAGES = [
    code.strip().lower()
    for code in os.environ.get("TRANSCRIPTION_ALLOWED_LANGUAGES", "").split(",")
    if code.strip()
]

# Redis / Celery
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TIMEZONE = "UTC"

# Cache — used for the regenerate rate limit (SPEC §6.5). Redis so the
# counter is shared between runserver/gunicorn workers; tests override to
# LocMem (see tests/settings_test.py).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("CACHE_REDIS_URL", REDIS_URL),
    }
}

# SPEC §6.5 — max regenerations of one artifact per minute.
REGENERATE_LIMIT_PER_MINUTE = _env_int("REGENERATE_LIMIT_PER_MINUTE", 3)

# Feature flags
ENABLE_DIARIZATION = _env_bool("ENABLE_DIARIZATION", False)
ENABLE_FACE_TRACKING = _env_bool("ENABLE_FACE_TRACKING", False)
ENABLE_AI_THUMBNAILS = _env_bool("ENABLE_AI_THUMBNAILS", False)

# Event publishing — tests toggle this off to avoid touching Redis.
EVENTS_ENABLED = _env_bool("EVENTS_ENABLED", default=True)

# DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_THROTTLE_CLASSES": ["api.throttles.ApiAnonThrottle"],
    "EXCEPTION_HANDLER": "api.exception_handler.structured_exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# CORS: the Vite dev server proxies /api so same-origin is the normal case.
# Wide-open only in DEBUG; in production list the SPA origins explicitly in
# CORS_ALLOWED_ORIGINS (comma-separated) or leave empty for same-origin only.
_cors_origins = [
    o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
CORS_ALLOW_ALL_ORIGINS = DEBUG and not _cors_origins
CORS_ALLOWED_ORIGINS = _cors_origins
CORS_ALLOW_CREDENTIALS = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {"level": "INFO", "propagate": True},
        "celery": {"level": "INFO", "propagate": True},
    },
}
