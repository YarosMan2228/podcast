"""workers.tasks — transition helper, start_job, transcribe_job_task."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jobs.models import (
    Analysis,
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Job,
    JobStatus,
    SourceType,
)
from pipeline.analysis import AnalysisError
from pipeline.ingestion import IngestionError
from pipeline.transcription import TranscriptionError
from workers.tasks import (
    InvalidTransition,
    NUM_VIDEO_CLIPS,
    analyze_job_task,
    orchestrate_artifacts,
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
    with patch("workers.tasks.transcribe_job") as tr, patch(
        "workers.tasks.analyze_job_task.apply_async"
    ) as next_task, patch("workers.tasks.publish") as pub:
        transcribe_job_task.apply_async(args=[str(job.id)])

    tr.assert_called_once_with(str(job.id))
    next_task.assert_called_once_with(args=[str(job.id)])
    job.refresh_from_db()
    assert job.status == JobStatus.TRANSCRIBING
    pub.assert_called_once_with(str(job.id), "status_changed", {"status": "TRANSCRIBING"})


def test_transcribe_task_fails_job_on_transcription_error() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.INGESTING)
    with patch(
        "workers.tasks.transcribe_job",
        side_effect=TranscriptionError("TRANSCRIPTION_EMPTY", "no speech"),
    ), patch("workers.tasks.analyze_job_task.apply_async") as next_task:
        transcribe_job_task.apply_async(args=[str(job.id)])

    next_task.assert_not_called()
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert "TRANSCRIPTION_EMPTY" in job.error
    assert "no speech" in job.error


def test_transcribe_task_rejects_wrong_start_state() -> None:
    """Must not move a job that isn't currently INGESTING."""
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.PENDING)
    with pytest.raises(InvalidTransition):
        transcribe_job_task.apply_async(args=[str(job.id)])


# -------------------- analyze_job_task --------------------


def test_analyze_task_uses_standard_decorator() -> None:
    assert analyze_job_task.max_retries == 3
    assert analyze_job_task.soft_time_limit == 300
    assert analyze_job_task.time_limit == 330
    assert analyze_job_task.acks_late is True


def test_analyze_task_transitions_transcribing_to_analyzing() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.TRANSCRIBING)
    # Stub orchestrate_artifacts so we isolate the analyze transition —
    # its own fan-out behaviour is covered further down in this file.
    with patch("workers.tasks.analyze_job") as an, patch(
        "workers.tasks.orchestrate_artifacts.apply_async"
    ) as orch, patch("workers.tasks.publish") as pub:
        analyze_job_task.apply_async(args=[str(job.id)])

    an.assert_called_once_with(str(job.id))
    orch.assert_called_once_with(args=[str(job.id)])
    job.refresh_from_db()
    assert job.status == JobStatus.ANALYZING
    pub.assert_called_once_with(str(job.id), "status_changed", {"status": "ANALYZING"})


def test_analyze_task_fails_job_on_analysis_error() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.TRANSCRIBING)
    with patch(
        "workers.tasks.analyze_job",
        side_effect=AnalysisError("ANALYSIS_INVALID_JSON", "no valid json"),
    ):
        analyze_job_task.apply_async(args=[str(job.id)])

    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert "ANALYSIS_INVALID_JSON" in job.error
    assert "no valid json" in job.error


def test_analyze_task_rejects_wrong_start_state() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.INGESTING)
    with pytest.raises(InvalidTransition):
        analyze_job_task.apply_async(args=[str(job.id)])


# -------------------- orchestrate_artifacts --------------------


def _make_analysis(job: Job, *, candidate_count: int) -> Analysis:
    """Build an Analysis row with ``candidate_count`` clip candidates."""
    return Analysis.objects.create(
        job=job,
        episode_title="t",
        hook="h",
        themes_json=[],
        chapters_json=[],
        clip_candidates_json=[
            {
                "start_ms": i * 60_000,
                "end_ms": i * 60_000 + 45_000,
                "virality_score": 9 - i,
                "reason": "x",
                "hook_text": f"h{i}",
            }
            for i in range(candidate_count)
        ],
        quotes_json=[],
        claude_model="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
    )


def test_orchestrate_task_uses_standard_decorator() -> None:
    assert orchestrate_artifacts.max_retries == 3
    assert orchestrate_artifacts.soft_time_limit == 300
    assert orchestrate_artifacts.time_limit == 330
    assert orchestrate_artifacts.acks_late is True


def test_orchestrate_creates_five_video_clips_and_enqueues_each() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.ANALYZING)
    _make_analysis(job, candidate_count=10)  # more than we'll schedule

    with patch(
        "workers.video_clip_worker.generate_video_clip.apply_async"
    ) as kick:
        orchestrate_artifacts.apply_async(args=[str(job.id)])

    job.refresh_from_db()
    assert job.status == JobStatus.GENERATING
    artifacts = list(Artifact.objects.filter(job=job).order_by("index"))
    assert len(artifacts) == NUM_VIDEO_CLIPS
    assert {a.type for a in artifacts} == {ArtifactType.VIDEO_CLIP}
    assert {a.status for a in artifacts} == {ArtifactStatus.QUEUED}
    assert [a.index for a in artifacts] == list(range(NUM_VIDEO_CLIPS))
    # One apply_async per artifact, all on the "video" queue.
    assert kick.call_count == NUM_VIDEO_CLIPS
    for call in kick.call_args_list:
        assert call.kwargs.get("queue") == "video"


def test_orchestrate_clamps_to_number_of_available_candidates() -> None:
    """Short episode: Claude returned 2 candidates, so we ship 2 clip slots."""
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.ANALYZING)
    _make_analysis(job, candidate_count=2)

    with patch("workers.video_clip_worker.generate_video_clip.apply_async") as kick:
        orchestrate_artifacts.apply_async(args=[str(job.id)])

    assert Artifact.objects.filter(job=job).count() == 2
    assert kick.call_count == 2


def test_orchestrate_fails_job_with_zero_candidates() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.ANALYZING)
    _make_analysis(job, candidate_count=0)

    with patch("workers.video_clip_worker.generate_video_clip.apply_async") as kick:
        orchestrate_artifacts.apply_async(args=[str(job.id)])

    kick.assert_not_called()
    job.refresh_from_db()
    # Transitions through GENERATING on its way to FAILED — that's fine,
    # the terminal state is what matters.
    assert job.status == JobStatus.FAILED
    assert "ORCHESTRATE_NO_CLIPS" in job.error


def test_orchestrate_rejects_wrong_start_state() -> None:
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.TRANSCRIBING)
    _make_analysis(job, candidate_count=5)
    with pytest.raises(InvalidTransition):
        orchestrate_artifacts.apply_async(args=[str(job.id)])


def test_orchestrate_is_idempotent_on_rerun() -> None:
    """A retry after partial progress must not create a 2nd set of rows."""
    job = Job.objects.create(source_type=SourceType.FILE, status=JobStatus.ANALYZING)
    _make_analysis(job, candidate_count=5)

    with patch("workers.video_clip_worker.generate_video_clip.apply_async"):
        orchestrate_artifacts.apply_async(args=[str(job.id)])

    # Simulate the orchestrator being kicked a second time with the job
    # already at GENERATING — mimics a worker crash + requeue.
    Job.objects.filter(id=job.id).update(status=JobStatus.ANALYZING)
    with patch("workers.video_clip_worker.generate_video_clip.apply_async"):
        orchestrate_artifacts.apply_async(args=[str(job.id)])

    # Same 5 rows — update_or_create keyed on (job, type, index) prevents
    # duplicates (rules/celery-tasks.md §3).
    assert Artifact.objects.filter(job=job).count() == NUM_VIDEO_CLIPS
