"""``/api/auth/session`` — SPA login against the optional access token.

``GET``  → ``{"required": bool, "authenticated": bool}`` so the frontend
          knows whether to show the token prompt.
``POST`` → ``{"token": "..."}``; on match sets the HttpOnly cookie the
          middleware accepts and returns ``{"authenticated": true}``,
          otherwise ``401 AUTH_INVALID``.
``DELETE`` → clears the cookie.
"""
from __future__ import annotations

from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import ApiError
from api.middleware import (
    COOKIE_NAME,
    HEADER_NAME,
    configured_token,
    set_access_cookie,
    token_matches,
)


class AuthInvalid(ApiError):
    status_code = 401
    default_code = "AUTH_INVALID"
    default_message = "Access token is not valid."


@api_view(["GET", "POST", "DELETE"])
def session(request: Request) -> Response:
    required = bool(configured_token())

    if request.method == "GET":
        authenticated = (not required) or token_matches(
            request.COOKIES.get(COOKIE_NAME)
        ) or token_matches(request.META.get(HEADER_NAME))
        return Response({"required": required, "authenticated": authenticated})

    if request.method == "DELETE":
        response = Response({"authenticated": not required})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    raw = (request.data or {}).get("token") if request.data else None
    if not required:
        return Response({"authenticated": True})
    if not token_matches(str(raw) if raw is not None else None):
        raise AuthInvalid()
    response = Response({"authenticated": True})
    set_access_cookie(response, request, configured_token())
    return response
