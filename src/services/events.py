"""Job-scoped event publisher for SSE fan-out.

Frontend's `useJob(jobId)` subscribes (via the Day-4 SSE endpoint) to
Redis channel ``job:<job_id>``. Workers call :func:`publish` to push a
status update, artifact-ready notification, etc.

Failures are logged but never raised — a flaky Redis must not block the
pipeline; the client will just miss a tick and recover on next event.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.REDIS_URL)
    return _client


def reset_client() -> None:
    """Drop the cached client (tests use this after patching settings)."""
    global _client
    _client = None


def publish(
    job_id: str, event_type: str, payload: dict[str, Any] | None = None
) -> bool:
    """Publish ``{event, data}`` JSON on channel ``job:<job_id>``.

    Returns True if the message was handed off to Redis, False if the
    publisher is disabled or Redis is unreachable. Never raises.
    """
    if not getattr(settings, "EVENTS_ENABLED", True):
        return False

    channel = f"job:{job_id}"
    message = json.dumps({"event": event_type, "data": payload or {}})
    try:
        _get_client().publish(channel, message)
    except redis.RedisError as exc:
        logger.warning(
            "event_publish_failed",
            extra={
                "job_id": str(job_id),
                "event": event_type,
                "error": str(exc),
            },
        )
        return False
    return True
