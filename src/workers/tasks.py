"""Celery tasks + shared helpers for job orchestration.

Lives per ``.claude/rules/celery-tasks.md`` §4 (transition helper) and §1
(standard decorator).
"""
from __future__ import annotations

import logging

from django.db import transaction

from core.celery import celery_app
from jobs.models import (
    Analysis,
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
    can_transition,
)
from pipeline.analysis import AnalysisError, analyze_job
from pipeline.ingestion import IngestionError, ingest_job
from pipeline.transcription import TranscriptionError, transcribe_job
from services.events import publish

logger = logging.getLogger(__name__)


# SPEC §5.1 — five parallel VIDEO_CLIP artifacts per episode. The
# orchestrator clamps against the actual number of clip_candidates Claude
# returned (a very short episode may yield fewer; SPEC §3.5 / §4.5
# guarantee at least 2 for anything worth clipping).
NUM_VIDEO_CLIPS = 5


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

    # .claude/rules/celery-tasks.md §7: dispatch the next stage only after
    # the ingestion updates are committed (ingest_job's UPDATE is outside a
    # wrapping transaction here, so Django has already committed on return).
    transcribe_job_task.apply_async(args=[job_id])
    logger.info("task_completed", extra={"task": "start_job", "job_id": job_id})


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=330,
    acks_late=True,
)
def transcribe_job_task(self, job_id: str) -> None:
    """Run Whisper transcription: INGESTING → TRANSCRIBING, call API, save row.

    Analysis dispatch is wired in the next Day-2 subtask; for now a successful
    transcription leaves the Job in ``TRANSCRIBING`` with a ``Transcript`` row
    persisted. ``TranscriptionError`` (empty / wrong language / noise / whisper
    down) moves the job to FAILED.
    """
    logger.info("task_started", extra={"task": "transcribe_job", "job_id": job_id})
    transition_job_status(job_id, JobStatus.INGESTING, JobStatus.TRANSCRIBING)

    try:
        transcribe_job(job_id)
    except TranscriptionError as exc:
        _fail_job(job_id, JobStatus.TRANSCRIBING, exc.code, exc.message)
        return

    analyze_job_task.apply_async(args=[job_id])
    logger.info("task_completed", extra={"task": "transcribe_job", "job_id": job_id})


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=330,
    acks_late=True,
)
def analyze_job_task(self, job_id: str) -> None:
    """Run Claude analysis: TRANSCRIBING → ANALYZING, one structured Claude call.

    Artifact fan-out is wired in Day 3; for now a successful analysis leaves
    the Job in ``ANALYZING`` with an ``Analysis`` row persisted.
    ``AnalysisError`` moves the job to FAILED.
    """
    logger.info("task_started", extra={"task": "analyze_job", "job_id": job_id})
    transition_job_status(job_id, JobStatus.TRANSCRIBING, JobStatus.ANALYZING)

    try:
        analyze_job(job_id)
    except AnalysisError as exc:
        _fail_job(job_id, JobStatus.ANALYZING, exc.code, exc.message)
        return

    # SPEC §9.4: analyze → orchestrate_artifacts fan-out.
    orchestrate_artifacts.apply_async(args=[job_id])
    logger.info("task_completed", extra={"task": "analyze_job", "job_id": job_id})


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=330,
    acks_late=True,
)
def orchestrate_artifacts(self, job_id: str) -> None:
    """Fan-out artifact creation: ANALYZING → GENERATING + enqueue workers.

    Day-3 scope creates only the ``VIDEO_CLIP`` artifacts (Person A's
    territory). Text / graphic artifact fan-out is layered on in Day 4
    once Person B's workers land — the orchestrator is the single point
    where that happens, so extending it is a one-line change.

    SPEC §5.4: artifact.index encodes the candidate position in the
    virality-sorted list, so ``clip_candidates_json[index]`` is what the
    video worker will pick up. The list is already deduped / sorted by
    ``pipeline.analysis`` before persistence.
    """
    logger.info(
        "task_started", extra={"task": "orchestrate_artifacts", "job_id": job_id}
    )
    transition_job_status(job_id, JobStatus.ANALYZING, JobStatus.GENERATING)

    try:
        analysis = Analysis.objects.get(job_id=job_id)
    except Analysis.DoesNotExist:
        # Shouldn't happen — analyze_job_task only fires us after the
        # Analysis row is committed — but fail loudly so we notice.
        _fail_job(
            job_id,
            JobStatus.GENERATING,
            "ORCHESTRATE_NO_ANALYSIS",
            "Analysis row missing at fan-out time",
        )
        return

    candidates = list(analysis.clip_candidates_json or [])
    clip_count = min(NUM_VIDEO_CLIPS, len(candidates))

    if clip_count == 0:
        # Degenerate: Claude returned zero clip_candidates. Don't silently
        # leave the job in GENERATING forever — surface the problem.
        _fail_job(
            job_id,
            JobStatus.GENERATING,
            "ORCHESTRATE_NO_CLIPS",
            "Analysis returned no clip_candidates — nothing to render",
        )
        return

    # Deferred import: the worker module pulls in ffmpeg-related helpers
    # that the ingestion / analysis test paths shouldn't have to load.
    from workers.video_clip_worker import generate_video_clip

    for idx in range(clip_count):
        artifact, _ = Artifact.objects.update_or_create(
            job_id=job_id,
            type=ArtifactType.VIDEO_CLIP,
            index=idx,
            defaults={
                "status": ArtifactStatus.QUEUED,
                "metadata_json": {},
                "error": None,
            },
        )
        generate_video_clip.apply_async(args=[str(artifact.id)], queue="video")

    logger.info(
        "task_completed",
        extra={
            "task": "orchestrate_artifacts",
            "job_id": job_id,
            "video_clip_count": clip_count,
        },
    )
