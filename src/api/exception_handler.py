"""DRF exception handler that emits SPEC §1.6 envelope shape."""
from __future__ import annotations

import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler

from api.errors import ApiError

logger = logging.getLogger(__name__)


def structured_exception_handler(exc, context):
    if isinstance(exc, ApiError):
        return Response(exc.as_envelope(), status=exc.status_code)

    response = drf_default_handler(exc, context)
    if response is None:
        logger.exception("unhandled_api_error", extra={"view": context.get("view")})
        return Response(
            {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error."}},
            status=500,
        )

    # Wrap generic DRF error output into our envelope.
    detail = response.data.get("detail") if isinstance(response.data, dict) else None
    code = getattr(exc, "default_code", "BAD_REQUEST")
    response.data = {
        "error": {
            "code": str(code).upper(),
            "message": str(detail) if detail else "Request failed.",
        }
    }
    return response
