"""Structured API errors per SPEC.md §1.6.

All API-facing error responses have shape:
    {"error": {"code": "...", "message": "...", "field": "..."}}

Views raise ApiError (or its subclasses); the DRF exception handler in
`api.exception_handler` converts that into the response envelope above.
"""
from __future__ import annotations

from rest_framework.exceptions import APIException


class ApiError(APIException):
    """Base class for all structured API errors."""

    status_code = 400
    default_code = "BAD_REQUEST"
    default_message = "Bad request."

    def __init__(
        self,
        code: str | None = None,
        message: str | None = None,
        field: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.code = code or self.default_code
        self.message = message or self.default_message
        self.field = field
        if status_code is not None:
            self.status_code = status_code
        super().__init__(detail=self.message, code=self.code)

    def as_envelope(self) -> dict:
        payload: dict = {"code": self.code, "message": self.message}
        if self.field is not None:
            payload["field"] = self.field
        return {"error": payload}


class UploadNoFile(ApiError):
    status_code = 400
    default_code = "UPLOAD_NO_FILE"
    default_message = "Field `file` is required."

    def __init__(self) -> None:
        super().__init__(field="file")


class UploadInvalidFormat(ApiError):
    status_code = 400
    default_code = "UPLOAD_INVALID_FORMAT"
    default_message = "Only audio/*, video/*, application/ogg are accepted."

    def __init__(self, mime: str | None = None) -> None:
        msg = (
            f"Unsupported content type `{mime}`. Expected audio/*, video/*, or application/ogg."
            if mime
            else self.default_message
        )
        super().__init__(message=msg, field="file")


class UploadTooLarge(ApiError):
    status_code = 413
    default_code = "UPLOAD_TOO_LARGE"
    default_message = "File exceeds the maximum size."

    def __init__(self, limit_mb: int) -> None:
        super().__init__(
            message=f"File exceeds {limit_mb}MB limit.",
            field="file",
        )


class UploadEmptyFile(ApiError):
    status_code = 400
    default_code = "UPLOAD_EMPTY_FILE"
    default_message = "Uploaded file is empty."

    def __init__(self) -> None:
        super().__init__(field="file")


class StorageError(ApiError):
    status_code = 500
    default_code = "STORAGE_ERROR"
    default_message = "Failed to persist upload."


class NotFound(ApiError):
    status_code = 404
    default_code = "NOT_FOUND"
    default_message = "Resource not found."


class JobNotFound(ApiError):
    """SPEC §9.3 — ``GET /api/jobs/:id`` returns this when the uuid is unknown."""

    status_code = 404
    default_code = "JOB_NOT_FOUND"
    default_message = "Job not found."

    def __init__(self, job_id: str | None = None) -> None:
        msg = f"Job {job_id!r} not found." if job_id else self.default_message
        super().__init__(message=msg, field="job_id")
