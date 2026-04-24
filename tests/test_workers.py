"""workers.tasks — transition helper, start_job, transcribe_job_task."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jobs.models import Job, JobStatus, SourceType
from pipeline.ingestion import IngestionError
from pipeline.transcription import TranscriptionError
from workers.tasks import (
    InvalidTransition,
    start_job,
    transcribe_job_task,
    transition_job_status,
)

pytestmark = pytest.mark.django_db


# -------------------- transition_job_status --------------------


def test_transition_happy_path_updates_and_publishes() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    with patch("workers.tasks.publish") as pub:
        transition_job_status(job.id, JobStatus.PENDING, JobStatus.INGESTING)

    job.refresh_from_db()
    assert job.status == JobStatus.INGESTING
    pub.assert_called_once_with(str(job.id), "status_changed", {"status": "INGESTING"})


def test_transition_rejects_illegal_target() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    with pytest.raises(InvalidTransition, match="not allowed"):
        transition_job_status(job.id, JobStatus.PENDING, JobStatus.COMPLETED)
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING  # untouched


def test_transition_rejects_from_status_mismatch() -> None:
    """Lost-race scenario: another worker already advanced the job."""
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.INGESTING)

    with pytest.raises(InvalidTransition, match="not in expected state"):
        transition_job_status(job.id, JobStatus.PENDING, JobStatus.INGESTING)

    job.refresh_from_db()
    assert job.status == JobStatus.INGESTING  # untouched


def test_transition_any_state_can_fail() -> None:
    """Per SPEC §1.1 the FAILED sink is reachable from any non-terminal state."""
    for start in (
        JobStatus.INGESTING,
        JobStatus.TRANSCRIBING,
        JobStatus.ANALYZING,
        JobStatus.GENERATING,
        JobStatus.PACKAGING,
    ):
        job = Job.objects.create(source_type=SourceType.FILE, status=start)
        transition_job_status(job.id, start, JobStatus.FAILED)
        job.refresh_from_db()
        assert job.status == JobStatus.FAILED


def test_transition_terminal_states_cannot_move() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.COMPLETED)
    with pytest.raises(InvalidTransition, match="not allowed"):
        transition_job_status(job.id, JobStatus.COMPLETED, JobStatus.FAILED)


# -------------------- start_job task --------------------


def test_start_job_transitions_to_ingesting_and_runs_ingestion() -> None:
    """Happy path: pending → ingesting, ingest_job invoked, next stage dispatched."""
    job = Job.objects.create(source_type=SourceType.FILE)
    with patch("workers.tasks.ingest_job") as ingest, patch(
        "workers.tasks.transcribe_job_task.apply_async"
    ) as next_task, patch("workers.tasks.publish") as pub:
        start_job.apply_async(args=[str(job.id)])

    ingest.assert_called_once_with(str(job.id))
    next_task.assert_called_once_with(args=[str(job.id)])
    job.refresh_from_db()
    assert job.status == JobStatus.INGESTING
    pub.assert_called_once_with(str(job.id), "status_changed", {"status": "INGESTING"})


def test_start_job_does_not_dispatch_next_stage_on_failure() -> None:
    """If ingestion fails, we must NOT hand control to the transcribe task."""
    job = Job.objects.create(source_type=SourceType.FILE)
    with patch(
        "workers.tasks.ingest_job",
        side_effect=IngestionError("INGESTION_NORMALIZE_FAILED", "ffmpeg exited 1"),
    ), patch("workers.tasks.transcribe_job_task.apply_async") as next_task:
        start_job.apply_async(args=[str(job.id)])

    next_task.assert_not_called()
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED


def test_start_job_uses_standard_decorator() -> None:
    """Enforce .claude/rules/celery-tasks.md §1 on every task we ship."""
    assert start_job.max_retries == 3
    assert start_job.soft_time_limit == 300
    assert start_job.time_limit == 330
    assert start_job.acks_late is True


def test_start_job_propagates_invalid_transition_in_eager_mode() -> None:
    """A job already past PENDING must not be re-transitioned by a second kick."""
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.INGESTING)
    with pytest.raises(InvalidTransition):
        start_job.apply_async(args=[str(job.id)])


def test_start_job_fails_job_on_ingestion_error() -> None:
    """IngestionError from the pipeline → job FAILED, error persisted, no raise."""
    job = Job.objects.create(source_type=SourceType.FILE)
    with patch(
        "workers.tasks.ingest_job",
        side_effect=IngestionError("INGESTION_DURATION_UNKNOWN", "bad metadata"),
    ), patch("workers.tasks.transcribe_job_task.apply_async"):
        start_job.apply_async(args=[str(job.id)])

    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert "INGESTION_DURATION_UNKNOWN" in job.error
    assert "bad metadata" in job.error


def test_start_job_ingestion_error_emits_failed_status_event() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    with patch(
        "workers.tasks.ingest_job",
        side_effect=IngestionError("INGESTION_NORMALIZE_FAILED", "ffmpeg exited 1"),
    ), patch("workers.tasks.transcribe_job_task.apply_async"), patch(
        "workers.tasks.publish"
    ) as pub:
        start_job.apply_async(args=[str(job.id)])

    assert pub.call_count == 2
    assert pub.call_args_list[0].args == (str(job.id), "status_changed", {"status": "INGESTING"})
    assert pub.call_args_list[1].args == (str(job.id), "status_changed", {"status": "FAILED"})


# -------------------- transcribe_job_task --------------------


def test_transcribe_task_uses_standard_decorator() -> None:
    assert transcribe_job_task.max_retries == 3
    assert transcribe_job_task.soft_time_limit == 300
    assert transcribe_job_task.time_limit == 330
    assert transcribe_job_task.acks_late is True


def test_transcribe_task_transitions_ingesting_to_transcribing() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.INGESTING)
    with patch("workers.tasks.transcribe_job") as tr, patch("workers.tasks.publish") as pub:
        transcribe_job_task.apply_async(args=[str(job.id)])

    tr.assert_called_once_with(str(job.id))
    job.refresh_from_db()
    assert job.status == JobStatus.TRANSCRIBING
    pub.assert_called_once_with(str(job.id), "status_changed", {"status": "TRANSCRIBING"})


def test_transcribe_task_fails_job_on_transcription_error() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.INGESTING)
    with patch(
        "workers.tasks.transcribe_job",
        side_effect=TranscriptionError("TRANSCRIPTION_EMPTY", "no speech"),
    ):
        transcribe_job_task.apply_async(args=[str(job.id)])

    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert "TRANSCRIPTION_EMPTY" in job.error
    assert "no speech" in job.error


def test_transcribe_task_rejects_wrong_start_state() -> None:
    """Must not move a job that isn't currently INGESTING."""
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.PENDING)
    with pytest.raises(InvalidTransition):
        transcribe_job_task.apply_async(args=[str(job.id)])
