"""Ingestion — persists an upload and runs ffmpeg/ffprobe on the Job's raw media.

Split into three concerns:

1. ``save_upload`` — writes the uploaded bytes to disk and creates the Job row
   (called from the upload view, before Celery is involved).
2. ``normalize_to_wav`` / ``probe_duration_sec`` — thin subprocess wrappers
   around ffmpeg/ffprobe (SPEC §2.4, .claude/rules/ffmpeg-usage.md).
3. ``ingest_job`` — the Celery-task entry point that chains (2) for a given
   job_id and enforces ``MAX_EPISODE_DURATION_MIN``.

Raises ``IngestionError`` for pipeline failures — the Celery task converts
those into a ``FAILED`` transition. ``StorageError`` (from save_upload) is an
``ApiError`` because it's surfaced synchronously through the upload view.
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from api.errors import StorageError
from jobs.models import Job, SourceType

logger = logging.getLogger(__name__)


ACCEPTED_MIME_PREFIXES: tuple[str, ...] = ("audio/", "video/")
ACCEPTED_MIME_EXACT: frozenset[str] = frozenset({"application/ogg"})

# ffmpeg is CPU-bound on long episodes; matches soft_time_limit for the
# ingestion Celery task so we surface ffmpeg stalls before Celery SIGTERMs us.
FFMPEG_TIMEOUT_SEC = 300
# ffprobe reads metadata only — should complete in under a second.
FFPROBE_TIMEOUT_SEC = 30

# Truncate ffmpeg/ffprobe stderr in logs (full output can be 100s of KB).
_STDERR_LOG_TAIL = 2000
_STDERR_EXC_TAIL = 500


class IngestionError(Exception):
    """Pipeline-level failure during ingestion.

    Carries a stable ``code`` (SPEC §2.5 naming) so the worker layer can log /
    persist / event-publish without re-parsing the message.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


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


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe wrappers (SPEC §2.4, .claude/rules/ffmpeg-usage.md)
# ---------------------------------------------------------------------------


def normalize_to_wav(
    input_path: str, output_path: str, *, job_id: str | None = None
) -> None:
    """Normalize *input_path* to mono 16kHz PCM WAV at *output_path*.

    Matches the command in SPEC §2.4 and the Whisper pre-processing convention
    in ``.claude/rules/ffmpeg-usage.md`` §3. On failure: logs the tail of
    stderr and raises ``IngestionError`` with code ``INGESTION_NORMALIZE_FAILED``.

    ``job_id`` is logging-correlation only — kept keyword-only so existing
    callers/tests don't need to change.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise IngestionError(
            "INGESTION_NORMALIZE_FAILED",
            f"ffmpeg timed out after {FFMPEG_TIMEOUT_SEC}s",
        ) from exc
    except FileNotFoundError as exc:
        # ffmpeg binary missing from PATH — treat as pipeline failure, not
        # code bug, so the job fails gracefully instead of crashing the worker.
        raise IngestionError(
            "INGESTION_NORMALIZE_FAILED",
            "ffmpeg binary not found on PATH",
        ) from exc

    if result.returncode != 0:
        logger.error(
            "ffmpeg_normalize_failed",
            extra={
                "job_id": job_id,
                "cmd": " ".join(cmd),
                "stderr": result.stderr[-_STDERR_LOG_TAIL:],
                "returncode": result.returncode,
            },
        )
        raise IngestionError(
            "INGESTION_NORMALIZE_FAILED",
            f"ffmpeg exited {result.returncode}: {result.stderr[-_STDERR_EXC_TAIL:]}",
        )

    out = Path(output_path)
    if not out.exists() or out.stat().st_size < 1024:
        raise IngestionError(
            "INGESTION_NORMALIZE_FAILED",
            f"Output missing or too small: {output_path}",
        )


def probe_duration_sec(path: str, *, job_id: str | None = None) -> float:
    """Return media duration in seconds via ``ffprobe``.

    Raises ``IngestionError`` (code ``INGESTION_DURATION_UNKNOWN``, per SPEC
    §2.5) when ffprobe can't produce a finite positive number — this covers
    corrupted metadata, non-media files, and ffprobe failures.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise IngestionError(
            "INGESTION_DURATION_UNKNOWN",
            f"ffprobe timed out after {FFPROBE_TIMEOUT_SEC}s",
        ) from exc
    except FileNotFoundError as exc:
        raise IngestionError(
            "INGESTION_DURATION_UNKNOWN",
            "ffprobe binary not found on PATH",
        ) from exc

    if result.returncode != 0:
        logger.error(
            "ffprobe_failed",
            extra={
                "job_id": job_id,
                "path": path,
                "stderr": result.stderr[-_STDERR_LOG_TAIL:],
                "returncode": result.returncode,
            },
        )
        raise IngestionError(
            "INGESTION_DURATION_UNKNOWN",
            f"ffprobe exited {result.returncode}: {result.stderr[-_STDERR_EXC_TAIL:]}",
        )

    raw = result.stdout.strip()
    try:
        duration = float(raw)
    except ValueError as exc:
        raise IngestionError(
            "INGESTION_DURATION_UNKNOWN",
            f"ffprobe returned non-numeric duration: {raw!r}",
        ) from exc

    if not math.isfinite(duration) or duration <= 0:
        raise IngestionError(
            "INGESTION_DURATION_UNKNOWN",
            f"ffprobe returned invalid duration: {duration}",
        )
    return duration


# ---------------------------------------------------------------------------
# Orchestration — called from the Celery task
# ---------------------------------------------------------------------------


def ingest_job(job_id: str) -> None:
    """Run the full ingestion step for *job_id*.

    Normalizes ``raw_media_path`` → ``normalized.wav`` next to the original,
    probes duration, and writes both back to the Job row. Enforces
    ``settings.MAX_EPISODE_DURATION_MIN`` (SPEC §2.4).

    Called from ``workers.tasks.start_job`` — kept out of that module so the
    worker layer stays thin and the pipeline logic is unit-testable without
    a Celery app.
    """
    job = Job.objects.get(id=job_id)

    # URL-sourced jobs reach the worker with no local file yet — the view
    # only persists the URL and dispatches the task. Pull the audio with
    # yt-dlp now, then continue down the same normalize/probe path as a
    # file upload.
    if not job.raw_media_path and job.source_type == SourceType.URL and job.source_url:
        from pipeline.url_ingestion import download_from_url

        dest_dir = Path(settings.MEDIA_ROOT) / "uploads" / str(job.id)
        downloaded = download_from_url(job.source_url, dest_dir, job_id=job_id)
        Job.objects.filter(id=job.id).update(
            raw_media_path=str(downloaded),
            original_filename=downloaded.name,
        )
        job.raw_media_path = str(downloaded)

    if not job.raw_media_path:
        raise IngestionError(
            "INGESTION_NO_SOURCE",
            f"Job {job_id} has no raw_media_path to ingest",
        )

    raw = Path(job.raw_media_path)
    if not raw.exists():
        raise IngestionError(
            "INGESTION_NO_SOURCE",
            f"raw_media_path does not exist: {raw}",
        )

    normalized = raw.parent / "normalized.wav"
    normalize_to_wav(str(raw), str(normalized), job_id=job_id)

    duration = probe_duration_sec(str(normalized), job_id=job_id)
    max_min = getattr(settings, "MAX_EPISODE_DURATION_MIN", 180)
    if duration > max_min * 60:
        raise IngestionError(
            "INGESTION_EPISODE_TOO_LONG",
            f"Episode too long: {duration:.1f}s > {max_min} min limit",
        )

    Job.objects.filter(id=job_id).update(
        normalized_wav_path=str(normalized),
        duration_sec=duration,
    )
    logger.info(
        "ingestion_completed",
        extra={
            "job_id": job_id,
            "normalized_wav_path": str(normalized),
            "duration_sec": duration,
        },
    )
