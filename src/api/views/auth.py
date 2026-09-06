"""``/api/auth/session`` — SPA login against the access gate.

``GET``    → ``{"required", "authenticated", "name", "is_admin"}``
``POST``   → ``{"token": "..."}``; on match sets the HttpOnly cookie and
            returns the same shape, otherwise ``401 AUTH_INVALID``.
``DELETE`` → clears the cookie.
"""
from __future__ import annotations

from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import ApiError
from api.middleware import COOKIE_NAME, set_access_cookie
from services.access import ADMIN, Principal, gating_enabled, resolve_principal


class AuthInvalid(ApiError):
    status_code = 401
    default_code = "AUTH_INVALID"
    default_message = "Access token is not valid."


def _payload(principal: Principal | None, required: bool) -> dict:
    authenticated = (not required) or principal is not None
    p = principal or ADMIN
    return {
        "required": required,
        "authenticated": authenticated,
        "name": p.name if authenticated else None,
        "is_admin": p.is_admin if authenticated else False,
    }


@api_view(["GET", "POST", "DELETE"])
def session(request: Request) -> Response:
    required = gating_enabled()
    current: Principal | None = getattr(request._request, "principal", None)

    if request.method == "GET":
        return Response(_payload(current, required))

    if request.method == "DELETE":
        response = Response(_payload(None, required))
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    raw = (request.data or {}).get("token") if request.data else None
    if not required:
        return Response(_payload(ADMIN, False))
    principal = resolve_principal(str(raw) if raw is not None else None)
    if principal is None:
        raise AuthInvalid()
    response = Response(_payload(principal, required))
    set_access_cookie(response, request._request, str(raw).strip())
    return response
