"""Access gate for the whole API + media tree (SECURITY.md #10 #17).

Modes (see ``services.access``): open (no-op), single shared token, or
multi-user with per-user keys. When gating is on, requests to ``/api/``
and ``/media/`` must present a credential via the ``X-Access-Token``
header, the ``pp_access`` cookie, or a one-time ``?token=`` query
parameter (which plants the cookie so <video>/<img>/download links keep
working). ``GET /api/health`` and ``/api/auth/session`` stay open.

The resolved ``Principal`` is attached as ``request.principal``; views use
it to scope jobs to their owner. The cookie is HttpOnly + SameSite=Lax
(+ Secure over HTTPS) and holds the secret itself; comparison is
constant-time / hash-based in ``services.access``.
"""
from __future__ import annotations

import json
from typing import Callable

from django.http import HttpRequest, HttpResponse

COOKIE_NAME = "pp_access"
HEADER_NAME = "HTTP_X_ACCESS_TOKEN"
QUERY_PARAM = "token"
OPEN_PATHS: frozenset[str] = frozenset({"/api/health", "/api/auth/session"})
COOKIE_MAX_AGE = 30 * 24 * 3600


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
        from services.access import ADMIN, gating_enabled, resolve_principal

        if not gating_enabled():
            request.principal = ADMIN
            return self.get_response(request)

        path = request.path
        if not (path.startswith("/api/") or path.startswith("/media/")):
            return self.get_response(request)
        if path.rstrip("/") in OPEN_PATHS:
            # Session probe/login resolve the credential themselves.
            request.principal = (
                resolve_principal(request.META.get(HEADER_NAME))
                or resolve_principal(request.COOKIES.get(COOKIE_NAME))
            )
            return self.get_response(request)

        principal = resolve_principal(request.META.get(HEADER_NAME)) or resolve_principal(
            request.COOKIES.get(COOKIE_NAME)
        )
        if principal is not None:
            request.principal = principal
            return self.get_response(request)

        query = request.GET.get(QUERY_PARAM)
        principal = resolve_principal(query)
        if principal is not None:
            request.principal = principal
            response = self.get_response(request)
            set_access_cookie(response, request, query.strip())
            return response
        return _unauthorized()
