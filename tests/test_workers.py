"""workers.tasks — transition helper and start_job stub."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings

from jobs.models import Job, JobStatus, SourceType
from workers.tasks import InvalidTransition, start_job, transition_job_status

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


def test_start_job_stub_transitions_pending_to_ingesting() -> None:
    job = Job.objects.create(source_type=SourceType.FILE)
    with patch("workers.tasks.publish") as pub:
        # Eager mode (CELERY_TASK_ALWAYS_EAGER=True) runs the task inline.
        start_job.apply_async(args=[str(job.id)])

    job.refresh_from_db()
    assert job.status == JobStatus.INGESTING
    pub.assert_called_once_with(str(job.id), "status_changed", {"status": "INGESTING"})


def test_start_job_uses_standard_decorator() -> None:
    """Enforce .claude/rules/celery-tasks.md §1 on every task we ship."""
    assert start_job.max_retries == 3
    assert start_job.soft_time_limit == 300
    assert start_job.time_limit == 330
    assert start_job.acks_late is True


@override_settings(START_JOB_STUB_SLEEP_SEC=0)
def test_start_job_respects_sleep_setting() -> None:
    """If the stub sleep is 0, the task finishes effectively instantly."""
    import time as _time

    job = Job.objects.create(source_type=SourceType.FILE)
    started = _time.monotonic()
    with patch("workers.tasks.publish"):
        start_job.apply_async(args=[str(job.id)])
    assert _time.monotonic() - started < 1.0


def test_start_job_propagates_invalid_transition_in_eager_mode() -> None:
    """A job already past PENDING must not be re-transitioned by a second kick."""
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.INGESTING)
    with pytest.raises(InvalidTransition):
        start_job.apply_async(args=[str(job.id)])
