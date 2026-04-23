"""Ingestion — persists an uploaded file and creates its Job row.

Does NOT run ffmpeg normalization, ffprobe, or start Celery yet; those
steps are added in the pipeline task wiring. Keep this module free of
DB-touching work outside `save_upload`.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from api.errors import StorageError
from jobs.models import Job, SourceType


ACCEPTED_MIME_PREFIXES: tuple[str, ...] = ("audio/", "video/")
ACCEPTED_MIME_EXACT: frozenset[str] = frozenset({"application/ogg"})


def is_accepted_mime(mime: str | None) -> bool:
    """SPEC §2.3 — accept audio/*, video/*, application/ogg."""
    if not mime:
        return False
    if mime in ACCEPTED_MIME_EXACT:
        return True
    return any(mime.startswith(prefix) for prefix in ACCEPTED_MIME_PREFIXES)


def _safe_basename(original: str | None) -> str:
    """Strip path components so a malicious ``original_filename`` of
    ``"../../etc/shadow"`` can't escape the upload directory.
    """
    name = os.path.basename((original or "").replace("\\", "/")) or "upload.bin"
    return name


def _write_chunks(dest: Path, chunks: Iterable[bytes]) -> int:
    written = 0
    with dest.open("wb") as fh:
        for chunk in chunks:
            fh.write(chunk)
            written += len(chunk)
    return written


def save_upload(upload: UploadedFile) -> Job:
    """Persist *upload* to ``MEDIA_ROOT/uploads/<job_id>/`` and create a Job.

    The caller (view) is responsible for having validated the file's mime,
    size, and non-emptiness — this function only translates IO failures
    into ``StorageError`` for the envelope.
    """
    job_id = uuid.uuid4()
    upload_dir = Path(settings.MEDIA_ROOT) / "uploads" / str(job_id)
    safe_name = _safe_basename(upload.name)
    dest = upload_dir / safe_name

    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
        written = _write_chunks(dest, upload.chunks())
    except OSError as exc:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise StorageError(message=f"Failed to persist upload: {exc}") from exc

    # A partial write means the client disconnected mid-upload — treat as
    # storage error so nothing downstream processes an incomplete file.
    if upload.size is not None and written != upload.size:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise StorageError(
            message=f"Partial write: expected {upload.size} bytes, wrote {written}"
        )

    with transaction.atomic():
        job = Job.objects.create(
            id=job_id,
            source_type=SourceType.FILE,
            original_filename=safe_name,
            raw_media_path=str(dest),
            file_size_bytes=written,
            mime_type=upload.content_type or None,
        )
    return job
