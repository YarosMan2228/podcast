"""DRF throttles (checklist #7 "rate limit APIs").

Two buckets, both per client IP, both backed by Django's cache (Redis in
prod, LocMem in tests):

* ``ApiAnonThrottle`` — global ceiling on every ``/api/`` view. Generous
  (default 300/min) so SSE reconnects + 5 s polling never trip it, but a
  script hammering the API does.
* ``UploadThrottle`` — new-job creation only (``upload`` + ``from_url``).
  Each job costs real money (Whisper + Claude), so the default is 20/hour.

Rates come from ``API_RATE_LIMIT`` / ``UPLOAD_RATE_LIMIT`` in ``.env``.
"""
from __future__ import annotations

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle


class ApiAnonThrottle(AnonRateThrottle):
    scope = "api"

    def get_rate(self) -> str | None:
        return getattr(settings, "API_RATE_LIMIT", None) or "300/min"


class UploadThrottle(AnonRateThrottle):
    scope = "upload"

    def get_rate(self) -> str | None:
        return getattr(settings, "UPLOAD_RATE_LIMIT", None) or "20/hour"
