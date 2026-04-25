"""GET /api/jobs/:id + SSE GET /api/jobs/:id/events (SPEC §9.3).

The REST view serialises the full Job state — status, progress counters,
analysis summary, per-artifact rows — in the shape documented in the
spec. The SSE view opens a Redis pub/sub subscription on the
``job:<id>`` channel that workers publish to via ``services.events``.

Design notes:

* **Stateless GET**: the view does not touch Redis — it's a pure DB read
  so it's also what the frontend hits on SSE reconnect to restore state
  (SPEC §10.6 ``useJob`` fallback).
* **SSE generator** lives outside the view body so it can be unit-tested
  without Django's streaming response plumbing. Response headers match
  the EventSource spec and defeat nginx buffering (``X-Accel-Buffering:
  no``) — otherwise events arrive in batches of ~4KB instead of
  immediately.
* **Keepalive** lines (``: keepalive\\n\\n``) every 15s keep proxies from
  timing the connection out (SPEC §9.3).
* **Disconnect cleanup**: the generator's ``finally`` block unsubscribes
  from Redis. Django drives the generator to exhaustion when the client
  goes away, so the finally is what releases the subscription.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Iterator

import redis
from django.conf import settings
from django.http import StreamingHttpResponse
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import ArtifactNotFound, InvalidTone, JobNotFound, PackageNotReady
from jobs.models import Artifact, ArtifactStatus, ArtifactType, Job, JobStatus

logger = logging.getLogger(__name__)


# Pub/sub idle wait before emitting a keepalive comment (SPEC §9.3).
SSE_KEEPALIVE_SEC: float = 15.0


# ---------------------------------------------------------------------------
# Serialisers — small helpers, not DRF Serializer classes. The shape is
# fixed by SPEC §9.3 so introducing a framework would only add indirection.
# ---------------------------------------------------------------------------


def _artifact_file_url(artifact: Artifact) -> str | None:
    """Relative URL under ``MEDIA_URL`` for a ready artifact, or ``None``.

    ``file_path`` is stored relative to ``MEDIA_ROOT`` (see
    ``workers.video_clip_worker``) so we just prefix ``MEDIA_URL``. The
    frontend composes the absolute URL against its own origin — we don't
    embed host here.
    """
    if not artifact.file_path:
        return None
    media_url = settings.MEDIA_URL or "/media/"
    if not media_url.endswith("/"):
        media_url += "/"
    return media_url + artifact.file_path.lstrip("/")


def _serialize_artifact(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": str(artifact.id),
        "type": artifact.type,
        "index": artifact.index,
        "status": artifact.status,
        "file_url": _artifact_file_url(artifact),
        "text_content": artifact.text_content,
        "metadata": artifact.metadata_json or {},
        "version": artifact.version,
        "error": artifact.error,
    }


def _progress_counters(artifacts: list[Artifact]) -> dict[str, int]:
    """SPEC §9.3 progress block — one counter per ArtifactStatus."""
    counters = {
        "total_artifacts": len(artifacts),
        "ready": 0,
        "processing": 0,
        "queued": 0,
        "failed": 0,
    }
    bucket = {
        ArtifactStatus.READY: "ready",
        ArtifactStatus.PROCESSING: "processing",
        ArtifactStatus.QUEUED: "queued",
        ArtifactStatus.FAILED: "failed",
    }
    for art in artifacts:
        key = bucket.get(art.status)
        if key:
            counters[key] += 1
    return counters


def _package_url_for(job: Job) -> str | None:
    """SPEC §8.2 — public URL of the packaged ZIP, or ``None`` until ready."""
    if not job.package_path:
        return None
    media_url = settings.MEDIA_URL or "/media/"
    if not media_url.endswith("/"):
        media_url += "/"
    return media_url + job.package_path.lstrip("/").replace("\\", "/")


def _serialize_job(job: Job) -> dict[str, Any]:
    """Full SPEC §9.3 response body."""
    artifacts = list(
        Artifact.objects.filter(job=job).order_by("type", "index")
    )
    analysis = getattr(job, "analysis", None)
    analysis_block = None
    if analysis is not None:
        analysis_block = {
            "episode_title": analysis.episode_title,
            "hook": analysis.hook,
        }

    return {
        "job_id": str(job.id),
        "status": job.status,
        "progress": _progress_counters(artifacts),
        "analysis": analysis_block,
        "artifacts": [_serialize_artifact(a) for a in artifacts],
        "package_url": _package_url_for(job),
        "error": job.error,
    }


# ---------------------------------------------------------------------------
# GET /api/jobs/:id
# ---------------------------------------------------------------------------


def _validate_job_id(job_id: str) -> str:
    """Reject non-uuid strings before we hit the DB.

    Without this, a garbage id would raise ``ValidationError`` from
    Django's UUID coercion and surface as a 500 via the default handler.
    """
    try:
        return str(uuid.UUID(job_id))
    except (ValueError, TypeError) as exc:
        raise JobNotFound(job_id=job_id) from exc


@api_view(["GET"])
def get_job(request: Request, job_id: str) -> Response:
    """Return SPEC §9.3 job payload. 404 ``JOB_NOT_FOUND`` on unknown id."""
    normalized = _validate_job_id(job_id)
    try:
        job = Job.objects.select_related("analysis").get(id=normalized)
    except Job.DoesNotExist as exc:
        raise JobNotFound(job_id=job_id) from exc
    return Response(_serialize_job(job))


# ---------------------------------------------------------------------------
# SSE stream — GET /api/jobs/:id/events
# ---------------------------------------------------------------------------


def _format_sse(event: str, data: dict[str, Any]) -> bytes:
    """Render one ``event:`` / ``data:`` pair in EventSource wire format.

    The trailing blank line is what the browser uses as a record
    terminator — without it, events are buffered indefinitely.
    """
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


def _sse_stream(
    job_id: str,
    pubsub: "redis.client.PubSub",
    *,
    keepalive_sec: float = SSE_KEEPALIVE_SEC,
) -> Iterator[bytes]:
    """Yield SSE-framed bytes for each Redis message on ``job:<job_id>``.

    Factored out of the view so tests can pump messages through without
    a real Redis. The caller is responsible for having already called
    ``pubsub.subscribe(...)``.
    """
    # Initial comment flushes headers + opens the stream on the client.
    yield b": connected\n\n"
    try:
        while True:
            message = pubsub.get_message(
                timeout=keepalive_sec, ignore_subscribe_messages=True
            )
            if message is None:
                # No event arrived inside the keepalive window — emit a
                # comment so proxies don't close the idle connection.
                yield b": keepalive\n\n"
                continue
            if message.get("type") != "message":
                continue

            raw = message.get("data")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                logger.warning(
                    "sse_bad_payload",
                    extra={"job_id": str(job_id), "raw": repr(raw)[:200]},
                )
                continue

            event = parsed.get("event", "message")
            data = parsed.get("data") or {}
            yield _format_sse(event, data)

            # Terminal events: the frontend's EventSource gets the final
            # payload and we politely close the subscription. The client
            # will drop the connection next; we exit the loop so the
            # generator's finally unsubscribes immediately.
            if event in {"completed", "job_failed"}:
                return
    finally:
        try:
            pubsub.unsubscribe()
            pubsub.close()
        except redis.RedisError as exc:
            logger.warning(
                "sse_unsubscribe_failed",
                extra={"job_id": str(job_id), "error": str(exc)},
            )


@api_view(["GET"])
def job_events(request: Request, job_id: str) -> StreamingHttpResponse:
    """SSE stream for job status + artifact updates.

    Subscribes to the same Redis channel ``services.events.publish``
    writes to; translates each JSON message into an ``event:/data:``
    pair. The view returns a ``StreamingHttpResponse`` so Django's
    WSGI/ASGI layer flushes chunks as they're yielded.
    """
    normalized = _validate_job_id(job_id)
    # Confirm the job exists before opening the stream — a 404 here is
    # better than a subscription that silently yields keepalives forever.
    if not Job.objects.filter(id=normalized).exists():
        raise JobNotFound(job_id=job_id)

    client = redis.Redis.from_url(settings.REDIS_URL)
    pubsub = client.pubsub()
    pubsub.subscribe(f"job:{normalized}")

    response = StreamingHttpResponse(
        _sse_stream(normalized, pubsub),
        content_type="text/event-stream",
    )
    # EventSource cache invariants + nginx buffering defeat. Proxies that
    # chunk-buffer the body will delay events by seconds otherwise.
    #
    # NOTE: don't set "Connection: keep-alive" — it's a hop-by-hop header
    # that WSGI (PEP 3333) forbids the application from emitting; Django's
    # runserver crashes on the assert with HTTP 500. Persistent connections
    # are the default for SSE under both runserver and gunicorn anyway.
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ---------------------------------------------------------------------------
# POST /api/artifacts/:id/regenerate — SPEC §5.3, §6.3
# ---------------------------------------------------------------------------

_VALID_TONES: frozenset[str] = frozenset(
    {"analytical", "casual", "punchy", "professional"}
)

_TEXT_ARTIFACT_TYPES: frozenset[str] = frozenset(
    {
        ArtifactType.LINKEDIN_POST,
        ArtifactType.TWITTER_THREAD,
        ArtifactType.SHOW_NOTES,
        ArtifactType.NEWSLETTER,
        ArtifactType.YOUTUBE_DESCRIPTION,
    }
)


def _dispatch_worker(artifact: Artifact, tone: str | None) -> None:
    """Dispatch the right worker task for a re-queued artifact."""
    if artifact.type == ArtifactType.VIDEO_CLIP:
        from workers.video_clip_worker import generate_video_clip

        generate_video_clip.apply_async(
            args=[str(artifact.id), True], queue="video"
        )

    elif artifact.type in _TEXT_ARTIFACT_TYPES:
        from workers.text_artifact_worker import (
            generate_linkedin_post,
            generate_newsletter,
            generate_show_notes,
            generate_twitter_thread,
            generate_youtube_description,
        )

        _task_map = {
            ArtifactType.LINKEDIN_POST: generate_linkedin_post,
            ArtifactType.TWITTER_THREAD: generate_twitter_thread,
            ArtifactType.SHOW_NOTES: generate_show_notes,
            ArtifactType.NEWSLETTER: generate_newsletter,
            ArtifactType.YOUTUBE_DESCRIPTION: generate_youtube_description,
        }
        task = _task_map[artifact.type]
        args: list = [str(artifact.id)]
        if tone:
            args.append(tone)
        task.apply_async(args=args, queue="text_artifacts")

    elif artifact.type == ArtifactType.QUOTE_GRAPHIC:
        from workers.quote_graphic_worker import generate_quote_graphic

        generate_quote_graphic.apply_async(args=[str(artifact.id)], queue="graphics")

    else:
        logger.warning(
            "regenerate_unknown_type",
            extra={"artifact_id": str(artifact.id), "type": artifact.type},
        )


@api_view(["POST"])
def regenerate_artifact(request: Request, artifact_id: str) -> Response:
    """Increment artifact version, reset to QUEUED, dispatch worker.

    Body (JSON, optional):
        ``{"tone": "casual"}``   — for text artifact tone variations.

    Returns 202:
        ``{"artifact_id": "...", "status": "QUEUED", "version": 2}``
    """
    try:
        normalized_id = str(uuid.UUID(artifact_id))
    except (ValueError, TypeError):
        raise ArtifactNotFound(artifact_id=artifact_id)

    try:
        artifact = Artifact.objects.get(id=normalized_id)
    except Artifact.DoesNotExist:
        raise ArtifactNotFound(artifact_id=artifact_id)

    tone: str | None = None
    if request.data:
        raw_tone = request.data.get("tone")
        if raw_tone is not None:
            if raw_tone not in _VALID_TONES:
                raise InvalidTone(raw_tone, set(_VALID_TONES))
            tone = raw_tone

    new_version = artifact.version + 1
    Artifact.objects.filter(id=artifact.id).update(
        version=new_version,
        status=ArtifactStatus.QUEUED,
        error=None,
    )
    artifact.version = new_version
    artifact.status = ArtifactStatus.QUEUED

    _dispatch_worker(artifact, tone)

    logger.info(
        "artifact_requeued",
        extra={
            "artifact_id": str(artifact.id),
            "type": artifact.type,
            "version": new_version,
            "tone": tone,
        },
    )

    return Response(
        {
            "artifact_id": str(artifact.id),
            "status": ArtifactStatus.QUEUED,
            "version": new_version,
        },
        status=202,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/:id/download — SPEC §9.3
# ---------------------------------------------------------------------------


# Stream chunk size for the ZIP body. 64KB keeps memory bounded while still
# matching the OS readahead, so throughput is dominated by the network.
_ZIP_STREAM_CHUNK = 64 * 1024


def _stream_file(path: "Path", chunk_size: int = _ZIP_STREAM_CHUNK) -> Iterator[bytes]:
    """Yield *path* in fixed-size chunks; closes the handle in ``finally``."""
    fh = path.open("rb")
    try:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                return
            yield chunk
    finally:
        fh.close()


@api_view(["GET"])
def download_package(request: Request, job_id: str) -> StreamingHttpResponse:
    """Stream the packaged ZIP for a COMPLETED job (SPEC §9.3).

    Errors:
        * ``404 JOB_NOT_FOUND`` — unknown UUID.
        * ``404 PACKAGE_NOT_READY`` — job is not COMPLETED yet, or the ZIP
          file is missing on disk (e.g. cleanup ran).
    """
    from pathlib import Path

    normalized = _validate_job_id(job_id)
    try:
        job = Job.objects.get(id=normalized)
    except Job.DoesNotExist as exc:
        raise JobNotFound(job_id=job_id) from exc

    if job.status != JobStatus.COMPLETED or not job.package_path:
        raise PackageNotReady(status=job.status)

    abs_path = Path(job.package_path)
    if not abs_path.is_absolute():
        abs_path = Path(settings.MEDIA_ROOT) / abs_path
    if not abs_path.exists():
        # The Job row says completed but the file vanished — surface the
        # same 404 so the frontend can display a recoverable error
        # ("re-run packaging") instead of a 500.
        logger.error(
            "package_file_missing",
            extra={"job_id": str(job.id), "package_path": job.package_path},
        )
        raise PackageNotReady(status=job.status)

    response = StreamingHttpResponse(
        _stream_file(abs_path),
        content_type="application/zip",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{abs_path.name}"'
    )
    response["Content-Length"] = str(abs_path.stat().st_size)
    response["Cache-Control"] = "no-store"
    return response
