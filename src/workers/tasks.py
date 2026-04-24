"""Celery tasks + shared helpers for job orchestration.

Lives per ``.claude/rules/celery-tasks.md`` §4 (transition helper) and §1
(standard decorator).
"""
from __future__ import annotations

import logging

from django.db import transaction

from core.celery import celery_app
from jobs.models import Job, JobStatus, can_transition
from pipeline.ingestion import IngestionError, ingest_job
from services.events import publish

logger = logging.getLogger(__name__)


class InvalidTransition(Exception):
    """Raised when a status transition is not permitted by SPEC §1.1 or
    when the stored status doesn't match ``from_status`` (lost race)."""


def transition_job_status(job_id: str, from_status: str, to_status: str) -> None:
    """Atomically flip Job.status if the current value matches ``from_status``.

    Uses ``UPDATE ... WHERE status = from_status`` so concurrent workers
    can't double-advance the state machine. Publishes a ``status_changed``
    SSE event on success.
    """
    if not can_transition(from_status, to_status):
        raise InvalidTransition(
            f"{from_status!r} → {to_status!r} is not allowed by SPEC §1.1"
        )

    with transaction.atomic():
        updated = Job.objects.filter(id=job_id, status=from_status).update(
            status=to_status
        )

    if updated == 0:
        current = (
            Job.objects.filter(id=job_id).values_list("status", flat=True).first()
        )
        raise InvalidTransition(
            f"Job {job_id} not in expected state "
            f"(stored={current!r}, expected={from_status!r}); "
            f"refusing to transition to {to_status!r}"
        )

    publish(str(job_id), "status_changed", {"status": to_status})


def _fail_job(job_id: str, from_status: str, code: str, message: str) -> None:
    """Record a pipeline failure: persist the error, flip to FAILED, emit event.

    ``transition_job_status`` already publishes a ``status_changed`` event;
    we piggy-back an ``artifact_failed``-shaped payload onto it by writing
    ``error`` to the Job row first so any `GET /api/jobs/:id` reader sees it.
    """
    Job.objects.filter(id=job_id).update(error=f"{code}: {message}")
    transition_job_status(job_id, from_status, JobStatus.FAILED)
    # 'message' is reserved on LogRecord — use 'error_message' in extras.
    logger.warning(
        "pipeline_failed",
        extra={
            "job_id": job_id,
            "from_status": from_status,
            "code": code,
            "error_message": message,
        },
    )


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=330,
    acks_late=True,
)
def start_job(self, job_id: str) -> None:
    """Run ingestion: transition PENDING → INGESTING, normalize + probe.

    Transcription dispatch is wired in a follow-up Day-2 task; for now a
    successful ingestion leaves the Job in ``INGESTING`` with
    ``normalized_wav_path`` and ``duration_sec`` populated. Pipeline errors
    (missing binary, ffmpeg non-zero, duration cap) move the job to FAILED.
    """
    logger.info("task_started", extra={"task": "start_job", "job_id": job_id})
    transition_job_status(job_id, JobStatus.PENDING, JobStatus.INGESTING)

    try:
        ingest_job(job_id)
    except IngestionError as exc:
        # Known pipeline failure — do not retry, just surface to the user.
        # .claude/rules/celery-tasks.md §6: permanent error → no raise.
        _fail_job(job_id, JobStatus.INGESTING, exc.code, exc.message)
        return

    logger.info("task_completed", extra={"task": "start_job", "job_id": job_id})
