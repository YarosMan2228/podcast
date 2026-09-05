"""EPISODE_THUMBNAIL worker (Pro) — 1280×720 cover with title, hook, brand.

Same lifecycle as the quote-graphic worker: PROCESSING → Playwright render
→ READY with ``file_path`` under ``artifacts/<job>/thumbnail.png``.
Queue ``graphics``. Regenerate re-renders with the current analysis
(title/hook may have been edited upstream) and the job's branding.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

from core.celery import celery_app
from jobs.models import Artifact
from workers.quote_graphic_worker import _mark_failed, _mark_processing, _mark_ready
from workers.text_artifact_worker import is_permanent_error, retry_countdown_sec

logger = logging.getLogger(__name__)

TEMPLATE_ID = "thumbnail_default"


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=330,
    acks_late=True,
    queue="graphics",
)
def generate_thumbnail(self, artifact_id: str) -> None:
    """Render the episode thumbnail PNG for an EPISODE_THUMBNAIL Artifact."""
    logger.info(
        "task_started", extra={"task": "generate_thumbnail", "artifact_id": artifact_id}
    )
    artifact: Artifact | None = None
    try:
        artifact = Artifact.objects.select_related("job__analysis").get(id=artifact_id)
        analysis = artifact.job.analysis
        title = (analysis.episode_title or "").strip() or "New episode"
        hook = (analysis.hook or "").strip()

        output_path = Path(settings.ARTIFACTS_ROOT) / str(artifact.job_id) / "thumbnail.png"
        _mark_processing(artifact)

        from pipeline.branding import branding_for_job
        from services.graphic_renderer import render_thumbnail_to_png

        branding = branding_for_job(artifact.job)
        render_thumbnail_to_png(
            title, hook, output_path, template_id=TEMPLATE_ID, branding=branding
        )

        rel_path = output_path.relative_to(Path(settings.MEDIA_ROOT)).as_posix()
        metadata: dict[str, Any] = {
            "title": title,
            "hook": hook,
            "template_id": TEMPLATE_ID,
            "resolution": "1280x720",
            "podcast_name": branding["podcast_name"],
            "brand_color": branding["brand_color"],
        }
        _mark_ready(artifact, rel_path, metadata)
        logger.info(
            "task_completed",
            extra={"task": "generate_thumbnail", "artifact_id": artifact_id},
        )
    except SoftTimeLimitExceeded:
        if artifact is not None:
            _mark_failed(artifact, "THUMBNAIL_TIMEOUT: soft_time_limit exceeded")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "task_failed", extra={"task": "generate_thumbnail", "artifact_id": artifact_id}
        )
        is_final = self.request.retries >= self.max_retries or is_permanent_error(exc)
        if is_final:
            if artifact is not None:
                _mark_failed(artifact, f"{type(exc).__name__}: {exc}")
            return
        raise self.retry(exc=exc, countdown=retry_countdown_sec(self.request.retries))
