"""POST /api/jobs/upload + POST /api/jobs/from_url — SPEC §2.3 (+ Pro branding)."""
from __future__ import annotations

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
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
from pipeline.branding import parse_branding
from pipeline.clip_options import parse_clip_options
from pipeline.ingestion import resolve_upload_mime, save_upload
from pipeline.url_ingestion import (
    UnsupportedHostError,
    UrlValidationError,
    validate_url,
)
from services.jobs_service import create_url_job
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
    """Multipart: ``file`` (required) + optional Pro fields
    ``podcast_name``, ``brand_color`` (#RRGGBB), ``logo`` (image ≤ 2 MB),
    ``clip_layout`` (pad|crop), ``caption_style`` (karaoke|clean|boxed)."""
    _gate_on_preflight()

    uploaded = request.FILES.get("file")
    if uploaded is None:
        raise UploadNoFile()
    if uploaded.size == 0:
        raise UploadEmptyFile()
    if uploaded.size > settings.MAX_UPLOAD_SIZE_BYTES:
        raise UploadTooLarge(limit_mb=settings.MAX_UPLOAD_SIZE_MB)
    mime = resolve_upload_mime(uploaded.content_type, uploaded.name)
    if mime is None:
        raise UploadInvalidFormat(mime=uploaded.content_type)
    branding = parse_branding(request.data, request.FILES)
    clip_options = parse_clip_options(request.data)

    job = save_upload(uploaded, mime_type=mime, branding=branding, clip_options=clip_options)
    # .claude/rules/celery-tasks.md §7: only dispatch after the Job row is
    # committed. save_upload's transaction.atomic() has already exited here.
    start_job.apply_async(args=[str(job.id)])
    return Response(
        {"job_id": str(job.id), "status": job.status},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@parser_classes([JSONParser, MultiPartParser, FormParser])
def from_url(request: Request) -> Response:
    """SPEC §2.3 — create a Job from a YouTube URL.

    Accepts JSON (``{"url": ...}``) or multipart (same field + optional
    branding incl. a ``logo`` file). The actual yt-dlp download happens in
    the Celery ingestion task so the HTTP response stays fast.
    """
    _gate_on_preflight()

    data = request.data or {}
    raw_url = data.get("url") if data else None
    try:
        url = validate_url(raw_url)
    except UrlValidationError as exc:
        raise UrlInvalid(url=raw_url if isinstance(raw_url, str) else None) from exc
    except UnsupportedHostError as exc:
        raise UrlUnsupportedHost(host=exc.host) from exc
    branding = parse_branding(data, request.FILES)
    clip_options = parse_clip_options(data)

    job = create_url_job(url, branding, clip_options)
    start_job.apply_async(args=[str(job.id)])
    return Response(
        {"job_id": str(job.id), "status": job.status},
        status=status.HTTP_201_CREATED,
    )
