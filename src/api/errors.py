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


class ServiceNotConfigured(ApiError):
    """503 — server can't accept jobs because external API keys are bad.

    Raised by upload views *before* a Job row is persisted, so the user
    gets a fast, actionable error instead of an upload that quietly fails
    30 seconds later inside the worker (SPEC §1.6 + STATUS §11 demo
    incident: placeholder OpenAI key surfaced only after Whisper 401).
    """

    status_code = 503
    default_code = "SERVICE_NOT_CONFIGURED"
    default_message = (
        "Server is not configured to process jobs. Check API keys in .env."
    )

    def __init__(self, detail: str | None = None) -> None:
        msg = detail or self.default_message
        super().__init__(message=msg)


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


class ArtifactNotFound(ApiError):
    """``POST /api/artifacts/:id/regenerate`` — unknown artifact id."""

    status_code = 404
    default_code = "ARTIFACT_NOT_FOUND"
    default_message = "Artifact not found."

    def __init__(self, artifact_id: str | None = None) -> None:
        msg = (
            f"Artifact {artifact_id!r} not found."
            if artifact_id
            else self.default_message
        )
        super().__init__(message=msg, field="artifact_id")


class UrlInvalid(ApiError):
    """``POST /api/jobs/from_url`` — body missing or non-http(s) URL."""

    status_code = 400
    default_code = "URL_INVALID"
    default_message = "URL is missing or not a valid http(s) URL."

    def __init__(self, url: str | None = None) -> None:
        msg = (
            f"URL {url!r} is not a valid http(s) URL."
            if url
            else self.default_message
        )
        super().__init__(message=msg, field="url")


class UrlUnsupportedHost(ApiError):
    """``POST /api/jobs/from_url`` — host outside the YouTube whitelist (SPEC §2.4)."""

    status_code = 400
    default_code = "URL_UNSUPPORTED_HOST"
    default_message = "URL host is not supported."

    def __init__(self, host: str | None = None) -> None:
        msg = (
            f"Host {host!r} is not in the YouTube-only MVP whitelist."
            if host
            else self.default_message
        )
        super().__init__(message=msg, field="url")


class UrlYtdlpFailed(ApiError):
    """``POST /api/jobs/from_url`` — yt-dlp returned an error (SPEC §2.5).

    Raised synchronously from the view when probing the URL fails before a
    Job is created. Long-running download failures inside the Celery task
    surface via ``IngestionError`` and the FAILED transition instead.
    """

    status_code = 422
    default_code = "URL_YTDLP_FAILED"
    default_message = "yt-dlp failed to extract media from URL."

    def __init__(self, detail: str | None = None) -> None:
        msg = (
            f"yt-dlp failed: {detail}" if detail else self.default_message
        )
        super().__init__(message=msg, field="url")


class PackageNotReady(ApiError):
    """``GET /api/jobs/:id/download`` — job is not in COMPLETED state yet."""

    status_code = 404
    default_code = "PACKAGE_NOT_READY"
    default_message = "Package is not ready yet."

    def __init__(self, status: str | None = None) -> None:
        msg = (
            f"Package is not ready (job status: {status})."
            if status
            else self.default_message
        )
        super().__init__(message=msg, field="job_id")


class InvalidTone(ApiError):
    """``POST /api/artifacts/:id/regenerate`` — unsupported tone value."""

    status_code = 400
    default_code = "INVALID_TONE"
    default_message = "Invalid tone."

    def __init__(self, tone: str, valid: set[str]) -> None:
        super().__init__(
            message=f"Tone {tone!r} is not allowed. Valid: {sorted(valid)}.",
            field="tone",
        )
