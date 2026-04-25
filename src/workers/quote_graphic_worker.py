"""Quote graphic Celery worker — SPEC §7.

Renders 5 PNG quote cards (1080×1080) from the top eligible quotes in the
Analysis. Each artifact picks its quote by ``artifact.index % len(eligible)``
and alternates between the two available templates.

Eligible quotes: 20–180 chars (SPEC §7.3).
If the Analysis has fewer than 5 eligible quotes, indices wrap around via
modulo so we always attempt to render something rather than silently skip.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from django.conf import settings

from core.celery import celery_app
from jobs.models import Artifact, ArtifactStatus
from services.events import publish

logger = logging.getLogger(__name__)

QUOTE_MIN_LEN = 20
QUOTE_MAX_LEN = 180

# Alternated by artifact.index — keeps the set visually varied.
TEMPLATE_CYCLE = ["minimal_dark", "gradient_purple"]


# ---------------------------------------------------------------------------
# Quote selection
# ---------------------------------------------------------------------------


def select_eligible_quotes(quotes_json: list[dict]) -> list[dict]:
    """Return quotes within the 20–180 char length window (SPEC §7.3)."""
    return [
        q
        for q in (quotes_json or [])
        if QUOTE_MIN_LEN <= len(q.get("text", "")) <= QUOTE_MAX_LEN
    ]


# ---------------------------------------------------------------------------
# DB helpers (same pattern as text_artifact_worker)
# ---------------------------------------------------------------------------


def _mark_processing(artifact: Artifact) -> None:
    Artifact.objects.filter(id=artifact.id).update(status=ArtifactStatus.PROCESSING)


def _mark_ready(
    artifact: Artifact, file_path: str, metadata: dict[str, Any]
) -> None:
    Artifact.objects.filter(id=artifact.id).update(
        status=ArtifactStatus.READY,
        file_path=file_path,
        metadata_json=metadata,
        error=None,
    )
    publish(
        str(artifact.job_id),
        "artifact_ready",
        {
            "artifact_id": str(artifact.id),
            "type": artifact.type,
            "index": artifact.index,
        },
    )
    from workers.tasks import check_and_trigger_packaging
    check_and_trigger_packaging(str(artifact.job_id))


def _mark_failed(artifact: Artifact, error_msg: str) -> None:
    Artifact.objects.filter(id=artifact.id).update(
        status=ArtifactStatus.FAILED,
        error=error_msg,
    )
    publish(
        str(artifact.job_id),
        "artifact_failed",
        {"artifact_id": str(artifact.id), "error": error_msg},
    )
    from workers.tasks import check_and_trigger_packaging
    check_and_trigger_packaging(str(artifact.job_id))


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=330,
    acks_late=True,
    queue="graphics",
)
def generate_quote_graphic(self, artifact_id: str) -> None:
    """Render the PNG quote card for a QUOTE_GRAPHIC Artifact."""
    logger.info(
        "task_started",
        extra={"task": "generate_quote_graphic", "artifact_id": artifact_id},
    )

    artifact: Artifact | None = None
    try:
        artifact = Artifact.objects.select_related("job__analysis").get(
            id=artifact_id
        )
        analysis = artifact.job.analysis
        eligible = select_eligible_quotes(list(analysis.quotes_json or []))

        if not eligible:
            raise ValueError(
                "No eligible quotes (20–180 chars) found in analysis — "
                "cannot render quote graphic"
            )

        # Wrap around if fewer quotes than artifact slots.
        slot = artifact.index % len(eligible)
        quote_data = eligible[slot]
        quote_text: str = quote_data.get("text", "")
        speaker: str = quote_data.get("speaker", "")
        template_id: str = TEMPLATE_CYCLE[artifact.index % len(TEMPLATE_CYCLE)]

        output_path = (
            Path(settings.ARTIFACTS_ROOT)
            / str(artifact.job_id)
            / f"quote_{artifact.index}.png"
        )

        _mark_processing(artifact)

        # Deferred import keeps Playwright out of the critical import path.
        from services.graphic_renderer import render_quote_to_png

        render_quote_to_png(quote_text, speaker, output_path, template_id=template_id)

        # Store path relative to MEDIA_ROOT for URL assembly in the API.
        rel_path = str(output_path.relative_to(Path(settings.MEDIA_ROOT)))

        metadata: dict[str, Any] = {
            "quote_text": quote_text,
            "speaker": speaker,
            "template_id": template_id,
            "source_quote_index": slot,
        }
        _mark_ready(artifact, rel_path, metadata)

        logger.info(
            "task_completed",
            extra={"task": "generate_quote_graphic", "artifact_id": artifact_id},
        )

    except Exception as exc:
        logger.exception(
            "task_failed",
            extra={"task": "generate_quote_graphic", "artifact_id": artifact_id},
        )
        is_final = self.request.retries >= self.max_retries
        if is_final and artifact is not None:
            _mark_failed(artifact, str(exc))
            return
        raise self.retry(exc=exc)
