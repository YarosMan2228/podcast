"""POST /api/jobs/upload + POST /api/jobs/from_url — SPEC §2.3."""
from __future__ import annotations

from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import (
    ServiceNotConfigured,
    UploadEmptyFile,
    UploadInvalidFormat,
    UploadNoFile,
    UploadTooLarge,
    UrlInvalid,
    UrlUnsupportedHost,
)
from jobs.models import Job, SourceType
from pipeline.ingestion import is_accepted_mime, save_upload
from pipeline.url_ingestion import (
    UnsupportedHostError,
    UrlValidationError,
    validate_url,
)
from services.preflight import check_api_keys, issues_to_message
from workers.tasks import start_job


def _gate_on_preflight() -> None:
    """Reject the request with 503 if API keys are unset/placeholder.

    Called from both upload entry points BEFORE any disk write or DB
    insert so a misconfigured server doesn't accept work it can't finish.
    Structural-only check (no network) — fast enough to run on every call,
    and a real key swap takes effect immediately.
    """
    issues = check_api_keys(probe_network=False)
    if issues:
        raise ServiceNotConfigured(detail=issues_to_message(issues))


@api_view(["POST"])
@parser_classes([MultiPartParser])
def upload(request: Request) -> Response:
    _gate_on_preflight()

    uploaded = request.FILES.get("file")
    if uploaded is None:
        raise UploadNoFile()
    if uploaded.size == 0:
        raise UploadEmptyFile()
    if uploaded.size > settings.MAX_UPLOAD_SIZE_BYTES:
        raise UploadTooLarge(limit_mb=settings.MAX_UPLOAD_SIZE_MB)
    if not is_accepted_mime(uploaded.content_type):
        raise UploadInvalidFormat(mime=uploaded.content_type)

    job = save_upload(uploaded)
    # .claude/rules/celery-tasks.md §7: only dispatch after the Job row is
    # committed. save_upload's transaction.atomic() has already exited here.
    start_job.apply_async(args=[str(job.id)])
    return Response(
        {"job_id": str(job.id), "status": job.status},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@parser_classes([JSONParser])
def from_url(request: Request) -> Response:
    """SPEC §2.3 — create a Job from a YouTube URL.

    The view only validates the URL whitelist + persists a Job row in
    ``PENDING``; the actual yt-dlp download happens in the Celery
    ingestion task (``pipeline.ingestion.ingest_job``) so the HTTP
    response stays fast and a slow download can't time out the request.
    """
    _gate_on_preflight()

    raw_url = (request.data or {}).get("url") if request.data else None
    try:
        url = validate_url(raw_url)
    except UrlValidationError as exc:
        raise UrlInvalid(url=raw_url if isinstance(raw_url, str) else None) from exc
    except UnsupportedHostError as exc:
        raise UrlUnsupportedHost(host=exc.host) from exc

    with transaction.atomic():
        job = Job.objects.create(
            source_type=SourceType.URL,
            source_url=url,
        )

    start_job.apply_async(args=[str(job.id)])
    return Response(
        {"job_id": str(job.id), "status": job.status},
        status=status.HTTP_201_CREATED,
    )
