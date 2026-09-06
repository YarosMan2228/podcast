"""Optional shared-secret gate for the whole API + media tree.

The MVP has no user accounts, so every ``/api/`` and ``/media/`` URL is
reachable by anyone who can reach the host — fine on localhost, not fine
the moment the demo is exposed on a public URL. Setting ``APP_ACCESS_TOKEN``
in ``.env`` turns this middleware on:

* requests must present the token via the ``X-Access-Token`` header, the
  ``pp_access`` cookie, or a one-time ``?token=`` query parameter (which
  sets the cookie so <video>/<img>/download links keep working);
* ``GET /api/health`` and ``/api/auth/session`` stay open so a load
  balancer can probe and the SPA can log in;
* the cookie is HttpOnly + SameSite=Lax (+ Secure over HTTPS) and holds
  the token itself, compared with ``hmac.compare_digest``.

Unset token → middleware is a no-op (local dev unchanged).
"""
from __future__ import annotations

import hmac
import json
from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

COOKIE_NAME = "pp_access"
HEADER_NAME = "HTTP_X_ACCESS_TOKEN"
QUERY_PARAM = "token"
OPEN_PATHS: frozenset[str] = frozenset({"/api/health", "/api/auth/session"})
COOKIE_MAX_AGE = 30 * 24 * 3600


def configured_token() -> str:
    return (getattr(settings, "APP_ACCESS_TOKEN", "") or "").strip()


def token_matches(candidate: str | None) -> bool:
    expected = configured_token()
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate.strip(), expected)


def set_access_cookie(response: HttpResponse, request: HttpRequest, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure(),
        path="/",
    )


def _unauthorized() -> HttpResponse:
    body = {
        "error": {
            "code": "AUTH_REQUIRED",
            "message": "This server requires an access token (X-Access-Token header).",
        }
    }
    resp = HttpResponse(json.dumps(body), status=401, content_type="application/json")
    resp["WWW-Authenticate"] = 'Token realm="podcast-pack"'
    return resp


class AccessTokenMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not configured_token():
            return self.get_response(request)

        path = request.path
        if not (path.startswith("/api/") or path.startswith("/media/")):
            return self.get_response(request)
        if path.rstrip("/") in OPEN_PATHS:
            return self.get_response(request)

        header = request.META.get(HEADER_NAME)
        cookie = request.COOKIES.get(COOKIE_NAME)
        query = request.GET.get(QUERY_PARAM)

        if token_matches(header) or token_matches(cookie):
            return self.get_response(request)
        if token_matches(query):
            response = self.get_response(request)
            set_access_cookie(response, request, configured_token())
            return response
        return _unauthorized()
