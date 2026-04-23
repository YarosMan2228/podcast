"""POST /api/jobs/upload — SPEC §2.3."""
from __future__ import annotations

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import (
    UploadEmptyFile,
    UploadInvalidFormat,
    UploadNoFile,
    UploadTooLarge,
)
from pipeline.ingestion import is_accepted_mime, save_upload


@api_view(["POST"])
@parser_classes([MultiPartParser])
def upload(request: Request) -> Response:
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
    return Response(
        {"job_id": str(job.id), "status": job.status},
        status=status.HTTP_201_CREATED,
    )
